-- Roster tables for D1. Mirrors labeldb/schema.sql minus users/sessions:
-- on Cloudflare, sign-in is handled by Cloudflare Access instead.
-- D1 enforces foreign keys by default, so ON DELETE CASCADE works.

CREATE TABLE bands (
    id                INTEGER PRIMARY KEY,
    name              TEXT NOT NULL UNIQUE COLLATE NOCASE,
    status            TEXT NOT NULL DEFAULT 'active'
                      CHECK (status IN ('active', 'onboarding', 'inactive')),
    intern            TEXT,
    sb_booking        TEXT CHECK (sb_booking    IN ('yes', 'no', 'in_process', 'onboarding')),
    sb_management     TEXT CHECK (sb_management IN ('yes', 'no', 'in_process', 'onboarding')),
    external_manager  TEXT,
    city              TEXT,
    state             TEXT,
    private_parties   TEXT CHECK (private_parties IN ('yes', 'no', 'possibly')),
    notes             TEXT
);

CREATE TABLE genres (
    id    INTEGER PRIMARY KEY,
    name  TEXT NOT NULL UNIQUE COLLATE NOCASE
);

CREATE TABLE band_genres (
    band_id   INTEGER NOT NULL REFERENCES bands(id)  ON DELETE CASCADE,
    genre_id  INTEGER NOT NULL REFERENCES genres(id) ON DELETE CASCADE,
    PRIMARY KEY (band_id, genre_id)
);

CREATE TABLE people (
    id     INTEGER PRIMARY KEY,
    name   TEXT NOT NULL,
    email  TEXT,
    phone  TEXT
);

CREATE TABLE band_members (
    id         INTEGER PRIMARY KEY,
    band_id    INTEGER NOT NULL REFERENCES bands(id)  ON DELETE CASCADE,
    person_id  INTEGER NOT NULL REFERENCES people(id) ON DELETE CASCADE,
    role       TEXT,
    UNIQUE (band_id, person_id)
);

CREATE TABLE links (
    id       INTEGER PRIMARY KEY,
    band_id  INTEGER NOT NULL REFERENCES bands(id) ON DELETE CASCADE,
    kind     TEXT NOT NULL,
    url      TEXT NOT NULL,
    label    TEXT
);

CREATE TABLE documents (
    id     INTEGER PRIMARY KEY,
    title  TEXT NOT NULL,
    url    TEXT NOT NULL
);

CREATE VIRTUAL TABLE band_search USING fts5(
    band_id UNINDEXED,
    content,
    tokenize = 'unicode61 remove_diacritics 2'
);
