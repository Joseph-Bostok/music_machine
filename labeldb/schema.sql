-- Label roster database. Every fact is stored once; relationships are
-- expressed with foreign keys instead of repeated text.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS bands (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE COLLATE NOCASE,
    status            TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'onboarding', 'inactive')),
    intern            TEXT,                -- intern assigned to the band
    sb_booking        TEXT CHECK (sb_booking    IN ('yes', 'no', 'in_process', 'onboarding')),
    sb_management     TEXT CHECK (sb_management IN ('yes', 'no', 'in_process', 'onboarding')),
    external_manager  TEXT,                -- set when management is handled outside the label
    city              TEXT,
    state             TEXT,                -- two-letter code when known
    private_parties   TEXT CHECK (private_parties IN ('yes', 'no', 'possibly')),
    notes             TEXT
);

-- Genres are many-to-many: "Indie Rock / Punk Rock" becomes two rows,
-- so filtering by one genre is an exact match instead of a substring hunt.
CREATE TABLE IF NOT EXISTS genres (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE IF NOT EXISTS band_genres (
    band_id   INTEGER NOT NULL REFERENCES bands(id)  ON DELETE CASCADE,
    genre_id  INTEGER NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
    PRIMARY KEY (band_id, genre_id)
);

-- One people row per human, so someone in two bands is entered once.
CREATE TABLE IF NOT EXISTS people (
    id     INTEGER PRIMARY KEY,
    name   TEXT NOT NULL,
    email  TEXT,
    phone  TEXT
);

CREATE TABLE IF NOT EXISTS band_members (
    id         INTEGER PRIMARY KEY,
    band_id    INTEGER NOT NULL REFERENCES bands(id)  ON DELETE CASCADE,
    person_id  INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    role       TEXT,
    UNIQUE (band_id, person_id)
);

-- All per-band URLs live here. Adding a new platform is a new `kind`
-- value, not a schema change.
CREATE TABLE IF NOT EXISTS links (
    id       INTEGER PRIMARY KEY,
    band_id  INTEGER NOT NULL REFERENCES bands(id) ON DELETE CASCADE,
    kind     TEXT NOT NULL,   -- band_sheet, artist_alignment, epk, epk_covers, instagram, ...
    url      TEXT NOT NULL,
    label    TEXT
);

-- Label-wide documents (the "IMPORTANT DOCS" column): not tied to a band.
CREATE TABLE IF NOT EXISTS documents (
    id     INTEGER PRIMARY KEY,
    title  TEXT NOT NULL,
    url    TEXT NOT NULL
);

-- Login. Passwords are stored only as salted scrypt hashes (see auth.py).
CREATE TABLE IF NOT EXISTS users (
    id             INTEGER PRIMARY KEY,
    email          TEXT NOT NULL UNIQUE COLLATE NOCASE,
    password_hash  TEXT NOT NULL,
    created_at     INTEGER NOT NULL DEFAULT (CAST(strftime('%s', 'now') AS INTEGER))
);

-- One row per signed-in browser. We store a SHA-256 of the cookie value,
-- not the value itself, so a leaked copy of the database can't be used
-- to impersonate anyone.
CREATE TABLE IF NOT EXISTS sessions (
    token_hash  TEXT PRIMARY KEY,
    user_id     INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at  INTEGER NOT NULL
);

-- Full-text index. One row per band holding every searchable string for
-- that band (name, genres, city, intern, member names, notes, ...).
-- Maintained by db.reindex_band() whenever a band or its children change.
CREATE VIRTUAL TABLE IF NOT EXISTS band_search USING fts5(
    band_id UNINDEXED,
    content,
    tokenize = 'unicode61 remove_diacritics 2'
);
