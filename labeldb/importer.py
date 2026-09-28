"""Import the label's master spreadsheet into the database.

Usage:
    python -m labeldb.importer "SB Master File.xlsx" [--db label.db] [--replace]

The spreadsheet mixes two kinds of data on one sheet:
  * one row per band (columns A-J), and
  * a free-standing list of label-wide documents ("IMPORTANT DOCS" and the
    unlabeled column beside it) that just happens to share rows with bands.
The importer separates them: bands go to `bands`, documents go to `documents`.

Links in the sheet are hyperlinks behind display text like "EPK" or
"Drive Link", so we read each cell's hyperlink target, not its text.
Anything that can't be imported cleanly is printed as an issue for a human
to fix rather than guessed at.
"""

import argparse
import re
import sys
from collections import defaultdict
from pathlib import Path

import openpyxl

from labeldb import db

# Normalized header text -> field name. Matching on headers instead of
# column letters keeps the importer working if columns get reordered.
HEADERS = {
    "starbloom bands": "name",
    "intern assigned": "intern",
    "sb booking": "sb_booking",
    "sb managment": "sb_management",   # (sic) as spelled in the sheet
    "sb management": "sb_management",
    "genre": "genre",
    "home state location": "location",
    "private parites": "private_parties",  # (sic)
    "private parties": "private_parties",
    "artist alignment": "artist_alignment",
    "epk link": "epk",
    "epk link w covers": "epk_covers",
    "important docs": "documents",
    "notes other info": "notes",
}

LINK_FIELDS = {"artist_alignment": "Artist alignment", "epk": "EPK", "epk_covers": "EPK w/ covers"}

YES_NO = {"yes": "yes", "no": "no", "in process": "in_process", "onboarding": "onboarding",
          "possibly": "possibly"}

# Genre spellings that mean the same thing.
GENRE_ALIASES = {"alt": "Alternative", "alt rock": "Alt Rock"}

# City shorthand whose state is unambiguous.
CITY_STATES = {"nyc": "NY"}


def clean(value):
    """Collapse whitespace; the sheet is full of trailing spaces."""
    if value is None:
        return None
    if isinstance(value, float) and value.is_integer():
        value = int(value)  # Excel stores 2025 as 2025.0
    text = re.sub(r"\s+", " ", str(value)).strip()
    return text or None


def normalize_header(value):
    """'Home State - Location ' -> 'home state location'."""
    return " ".join(re.sub(r"[^a-z]", " ", (clean(value) or "").lower()).split())


def parse_name(raw):
    """'Whisper Doll - onboarding' -> ('Whisper Doll', 'onboarding')."""
    match = re.match(r"^(.*?)\s*-\s*onboarding$", raw, flags=re.IGNORECASE)
    if match:
        return match.group(1), "onboarding"
    return raw, "active"


def parse_yes_no(raw, allowed):
    """Map free text to an enum value. Returns (value, extra_text).

    'no - Jon Borneman' -> ('no', 'Jon Borneman'): the text after the dash
    names who handles it instead, which we keep rather than discard.
    """
    if raw is None:
        return None, None
    head, _, tail = raw.partition(" - ")
    value = YES_NO.get(head.strip().lower())
    if value not in allowed:
        return None, raw
    return value, clean(tail)


def parse_location(raw):
    """'Charlotte - NC' -> ('Charlotte', 'NC'). Returns (city, state, ok)."""
    if raw is None:
        return None, None, True
    parts = [p.strip() for p in raw.split(" - ")]
    if len(parts) == 2 and re.fullmatch(r"[A-Za-z]{2}", parts[1]):
        return parts[0], parts[1].upper(), True
    if len(parts) == 1:
        return parts[0], CITY_STATES.get(parts[0].lower()), True
    # e.g. 'Atlanta - Charleston': two cities, no state. Keep as written.
    return raw, None, False


def parse_genres(raw):
    if raw is None:
        return []
    genres = []
    for part in raw.split("/"):
        name = clean(part)
        if not name:
            continue
        name = GENRE_ALIASES.get(name.lower(), name)
        if name.lower() not in (g.lower() for g in genres):
            genres.append(name)
    return genres


def read_sheet(path):
    """Parse the workbook into plain dicts. No database access here, so the
    parsing rules can be tested on their own."""
    wb = openpyxl.load_workbook(path)
    ws = wb.worksheets[0]
    rows = list(ws.iter_rows())
    header_row, body = rows[0], rows[1:]

    columns = {}
    for cell in header_row:
        field = HEADERS.get(normalize_header(cell.value))
        if field:
            columns[field] = cell.column - 1  # 0-based index into the row tuple
    missing = {"name", "genre", "location"} - columns.keys()
    if missing:
        raise ValueError(f"Spreadsheet is missing expected columns: {sorted(missing)}")

    # The documents list spans from IMPORTANT DOCS up to the NOTES column,
    # including the unlabeled column(s) in between.
    doc_cols = range(columns["documents"], columns.get("notes", columns["documents"] + 1)) \
        if "documents" in columns else range(0)

    bands, documents, issues = [], [], []

    def cell(row, field):
        idx = columns.get(field)
        return row[idx] if idx is not None and idx < len(row) else None

    for row in body:
        for idx in doc_cols:
            c = row[idx] if idx < len(row) else None
            if c is None or clean(c.value) is None:
                continue
            if c.hyperlink and c.hyperlink.target:
                documents.append({"title": clean(c.value), "url": c.hyperlink.target})
            else:
                issues.append(f"{c.coordinate}: document entry {clean(c.value)!r} has no link; skipped")

        name_cell = cell(row, "name")
        raw_name = clean(name_cell.value) if name_cell else None
        if raw_name is None:
            continue
        name, status = parse_name(raw_name)
        where = f"row {name_cell.row} ({name})"

        def value(field):
            c = cell(row, field)
            return clean(c.value) if c else None

        booking, booking_extra = parse_yes_no(value("sb_booking"), {"yes", "no", "in_process", "onboarding"})
        mgmt, manager = parse_yes_no(value("sb_management"), {"yes", "no", "in_process", "onboarding"})
        parties, parties_extra = parse_yes_no(value("private_parties"), {"yes", "no", "possibly"})
        for label, extra in (("SB Booking", booking_extra), ("Private parties", parties_extra)):
            if extra:
                issues.append(f"{where}: unrecognized {label} value {extra!r}")
        if mgmt is None and manager:
            issues.append(f"{where}: unrecognized SB Management value {manager!r}")
            manager = None

        city, state, loc_ok = parse_location(value("location"))
        if not loc_ok:
            issues.append(f"{where}: location {value('location')!r} has no state; stored as-is")

        intern = value("intern")
        if intern and intern.lower() in ("n/a", "onboarding"):
            intern = None

        links = []
        if name_cell.hyperlink and name_cell.hyperlink.target:
            links.append({"kind": "band_sheet", "label": "Band sheet", "url": name_cell.hyperlink.target})
        for field, label in LINK_FIELDS.items():
            c = cell(row, field)
            if c is None or clean(c.value) is None:
                continue
            if c.hyperlink and c.hyperlink.target:
                links.append({"kind": field, "label": label, "url": c.hyperlink.target})
            else:
                issues.append(f"{c.coordinate} ({name}): says {clean(c.value)!r} but has no link")

        bands.append({
            "name": name, "status": status, "intern": intern,
            "sb_booking": booking, "sb_management": mgmt, "external_manager": manager,
            "city": city, "state": state, "private_parties": parties,
            "notes": value("notes"), "genres": parse_genres(value("genre")),
            "links": links,
        })

    # The same EPK attached to two bands is almost always a copy/paste slip.
    by_url = defaultdict(list)
    for band in bands:
        for link in band["links"]:
            if link["kind"] in ("epk", "epk_covers", "artist_alignment"):
                by_url[link["url"]].append(band["name"])
    for url, names in by_url.items():
        if len(names) > 1:
            issues.append(f"Same link used for {', '.join(names)}: {url}")

    return bands, documents, issues


def load(conn, bands, documents):
    for band in bands:
        band_id = conn.execute(
            """INSERT INTO bands (name, status, intern, sb_booking, sb_management,
                   external_manager, city, state, private_parties, notes)
               VALUES (:name, :status, :intern, :sb_booking, :sb_management,
                   :external_manager, :city, :state, :private_parties, :notes)""",
            band,
        ).lastrowid
        db.set_genres(conn, band_id, band["genres"])
        for link in band["links"]:
            conn.execute(
                "INSERT INTO links (band_id, kind, url, label) VALUES (?, ?, ?, ?)",
                (band_id, link["kind"], link["url"], link["label"]),
            )
        db.reindex_band(conn, band_id)
    for doc in documents:
        conn.execute("INSERT INTO documents (title, url) VALUES (:title, :url)", doc)
    conn.commit()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("xlsx", type=Path)
    parser.add_argument("--db", type=Path, default=Path(db.default_path()),
                        help="database file (default: $LABEL_DB or ./label.db)")
    parser.add_argument("--replace", action="store_true",
                        help="delete existing data before importing")
    args = parser.parse_args(argv)

    bands, documents, issues = read_sheet(args.xlsx)

    conn = db.connect(args.db)
    db.init_db(conn)
    if conn.execute("SELECT COUNT(*) FROM bands").fetchone()[0]:
        if not args.replace:
            sys.exit(f"{args.db} already has data. Re-run with --replace to overwrite it.")
        # Users and sessions are deliberately kept: re-importing band data
        # shouldn't lock everyone out.
        for table in ("band_search", "documents", "links", "band_members",
                      "people", "band_genres", "genres", "bands"):
            conn.execute(f"DELETE FROM {table}")
    load(conn, bands, documents)

    print(f"Imported {len(bands)} bands and {len(documents)} label documents into {args.db}")
    if issues:
        print(f"\n{len(issues)} things to check in the spreadsheet:")
        for issue in issues:
            print(f"  - {issue}")


if __name__ == "__main__":
    main()
