# Label Roster

A small database and browser GUI for the label's band records. It replaces
the per-group spreadsheets with one searchable place.

## Setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
```

## Import the master spreadsheet (one time)

```bash
python -m labeldb.importer "SB Master File.xlsx"
```

This creates `label.db` and prints a list of things in the sheet that need
a human to fix, such as cells that say "EPK" but have no link. Re-running
refuses to overwrite existing data unless you pass `--replace`.

## Run the app

```bash
uvicorn labeldb.app:app
```

Open http://127.0.0.1:8000. Search matches as you type across band names,
members, genres, cities, interns, managers and notes. The dropdowns filter by
status, genre, state and intern. "Missing…" finds bands without a given
link type (for example, every band with no EPK).

API docs are at http://127.0.0.1:8000/docs.

## Layout

| File | Purpose |
|---|---|
| `labeldb/schema.sql` | Tables: bands, genres, people, band_members, links, documents, plus the full-text index |
| `labeldb/importer.py` | Reads the .xlsx (including hyperlinks behind cell text) and loads it |
| `labeldb/db.py` | Connection setup, search indexing, shared queries |
| `labeldb/app.py` | FastAPI JSON API that also serves the GUI |
| `labeldb/static/index.html` | The GUI: plain HTML/JS, no build step |

## Tests

```bash
pytest
```
