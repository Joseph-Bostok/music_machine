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

## Create a login

```bash
python -m labeldb.users add you@example.com
```

It prompts for a password. The same command also supports `passwd`, `remove` and `list`.

## Run the app

```bash
uvicorn labeldb.app:app
```

Open http://127.0.0.1:8000 and sign in. Search matches as you type across band names,
members, genres, cities, interns, managers and notes. The dropdowns filter by
status, genre, state and intern. "Missing…" finds bands without a given
link type (for example, every band with no EPK).

API docs are at http://127.0.0.1:8000/docs (sign in first).

To host it privately with a shareable link, there are two options:

- [DEPLOY-CLOUDFLARE.md](DEPLOY-CLOUDFLARE.md): **recommended.** No server to maintain. It runs as a
  Cloudflare Worker on D1 (hosted SQLite), and sign-in is handled by Cloudflare Access. The code is in `cloudflare/`.
- [DEPLOY.md](DEPLOY.md): this Python app on a free-tier Google Cloud VM with its own login, HTTPS
  and nightly backups.

## Layout

| File | Purpose |
|---|---|
| `labeldb/schema.sql` | Tables: bands, genres, people, band_members, links, documents, plus the full-text index |
| `labeldb/importer.py` | Reads the .xlsx (including hyperlinks behind cell text) and loads it |
| `labeldb/db.py` | Connection setup, search indexing, shared queries |
| `labeldb/app.py` | FastAPI JSON API that also serves the GUI |
| `labeldb/auth.py` | Password hashing (scrypt), sessions, login rate limiting |
| `labeldb/users.py` | Command-line account management |
| `labeldb/backup.py` | Consistent snapshots of the database, with optional Cloud Storage upload |
| `labeldb/static/` | The GUI and sign-in page: plain HTML/JS, no build step |
| `deploy/` | Server setup script, systemd units, admin wrapper |

## Tests

```bash
pytest
```
