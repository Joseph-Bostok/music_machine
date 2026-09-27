"""Database access: connection setup, search indexing, and shared queries."""

import re
import sqlite3
from pathlib import Path

SCHEMA_PATH = Path(__file__).with_name("schema.sql")
DEFAULT_DB_PATH = Path("label.db")


def connect(path=DEFAULT_DB_PATH):
    conn = sqlite3.connect(path, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    # Foreign keys are off by default in SQLite and must be enabled per connection.
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(conn):
    conn.executescript(SCHEMA_PATH.read_text())
    conn.commit()


# --- genres -----------------------------------------------------------------

def set_genres(conn, band_id, names):
    """Replace a band's genres with `names`, creating genre rows as needed."""
    conn.execute("DELETE FROM band_genres WHERE band_id = ?", (band_id,))
    for name in names:
        conn.execute("INSERT OR IGNORE INTO genres (name) VALUES (?)", (name,))
        genre_id = conn.execute(
            "SELECT id FROM genres WHERE name = ?", (name,)
        ).fetchone()["id"]
        conn.execute(
            "INSERT OR IGNORE INTO band_genres (band_id, genre_id) VALUES (?, ?)",
            (band_id, genre_id),
        )
    # Drop genres no band uses any more so filter dropdowns stay clean.
    conn.execute(
        "DELETE FROM genres WHERE id NOT IN (SELECT genre_id FROM band_genres)"
    )


def band_genres(conn, band_id):
    rows = conn.execute(
        """SELECT g.name FROM genres g
           JOIN band_genres bg ON bg.genre_id = g.id
           WHERE bg.band_id = ? ORDER BY g.name""",
        (band_id,),
    )
    return [r["name"] for r in rows]


# --- members ----------------------------------------------------------------

def add_member(conn, band_id, name, role=None, email=None, phone=None):
    """Attach a person to a band, reusing an existing person with the same name."""
    row = conn.execute(
        "SELECT id FROM people WHERE name = ? COLLATE NOCASE", (name,)
    ).fetchone()
    if row:
        person_id = row["id"]
        if email or phone:
            conn.execute(
                "UPDATE people SET email = COALESCE(?, email), phone = COALESCE(?, phone) WHERE id = ?",
                (email, phone, person_id),
            )
    else:
        person_id = conn.execute(
            "INSERT INTO people (name, email, phone) VALUES (?, ?, ?)",
            (name, email, phone),
        ).lastrowid
    return conn.execute(
        "INSERT INTO band_members (band_id, person_id, role) VALUES (?, ?, ?)",
        (band_id, person_id, role),
    ).lastrowid


# --- search -----------------------------------------------------------------

def reindex_band(conn, band_id):
    """Rebuild the full-text row for one band from its current data."""
    conn.execute("DELETE FROM band_search WHERE band_id = ?", (band_id,))
    band = conn.execute("SELECT * FROM bands WHERE id = ?", (band_id,)).fetchone()
    if band is None:
        return
    members = conn.execute(
        """SELECT p.name, bm.role FROM band_members bm
           JOIN people p ON p.id = bm.person_id WHERE bm.band_id = ?""",
        (band_id,),
    ).fetchall()
    parts = [
        band["name"], band["intern"], band["external_manager"], band["city"],
        band["state"], band["status"], band["notes"],
        *band_genres(conn, band_id),
        *(m["name"] for m in members),
        *(m["role"] for m in members),
    ]
    conn.execute(
        "INSERT INTO band_search (band_id, content) VALUES (?, ?)",
        (band_id, " ".join(p for p in parts if p)),
    )


def reindex_all(conn):
    conn.execute("DELETE FROM band_search")
    for row in conn.execute("SELECT id FROM bands").fetchall():
        reindex_band(conn, row["id"])


def fts_query(text):
    """Turn free text into a safe FTS5 prefix query.

    "celler dw" -> '"celler"* "dw"*' so results update while typing. Each
    word is quoted, which keeps user punctuation (quotes, dashes, colons)
    from being parsed as FTS5 operators.
    """
    words = re.findall(r"\w+", text, flags=re.UNICODE)
    return " ".join(f'"{w}"*' for w in words)
