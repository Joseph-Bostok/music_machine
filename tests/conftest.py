import os

import openpyxl
import pytest
from fastapi.testclient import TestClient
from openpyxl.worksheet.hyperlink import Hyperlink

# labeldb.app builds a module-level app on import; keep it off ./label.db.
os.environ.setdefault("LABEL_DB", ":memory:")

from labeldb import auth, db, importer  # noqa: E402
from labeldb.app import create_app  # noqa: E402

EMAIL, PASSWORD = "owner@example.com", "correct horse battery"

HEADER = ["Starbloom Bands ", "Intern Assigned", "SB BOOKING ", "SB MANAGMENT", "Genre ",
          "Home State - Location", "Private Parites", "Artist Alignment ", "EPK LINK ",
          "EPK LINK w/ Covers", "IMPORTANT DOCS", None, "NOTES / OTHER INFO "]


@pytest.fixture
def sample_xlsx(tmp_path):
    """A small workbook shaped like the real master file: messy whitespace,
    links behind display text, and label docs sharing rows with bands."""
    wb = openpyxl.Workbook()
    ws = wb.active
    ws.append(HEADER)
    ws.append(["Auroras Hope ", "n/a", "yes ", "yes ", "Grunge / Rock ", "Charlotte - NC ",
               "no ", "Drive Link ", "EPK", None, "Playlists research", 2025])
    ws.append(["Bedrumor  - onboarding", None, "yes ", "no - Jon Borneman",
               "Indie / Indie Pop / Alternative", "Durham - NC ", "possibly ", "Drive Link ", None,
               None, "Venues"])
    ws.append(["Current Blue ", "Frankie ", "in process ", "no", "Rock / Alt ",
               "Atlanta - Charleston ", "yes ", None, "EPK"])
    ws.append([None, None, None, None, None, None, None, None, None, None, "Music Festivals "])

    def link(coord, url):
        ws[coord].hyperlink = Hyperlink(ref=coord, target=url)

    link("A2", "https://example.com/auroras-sheet")
    link("H2", "https://example.com/auroras-alignment")
    link("I2", "https://example.com/shared-epk")
    link("K2", "https://example.com/playlists")
    # H3 says "Drive Link" but has no hyperlink: should be reported.
    link("K3", "https://example.com/venues")
    link("I4", "https://example.com/shared-epk")  # duplicate EPK: should be reported
    link("K5", "https://example.com/festivals")

    path = tmp_path / "master.xlsx"
    wb.save(path)
    return path


@pytest.fixture
def db_path(sample_xlsx, tmp_path):
    """A database loaded from the sample sheet, with one login account."""
    path = tmp_path / "label.db"
    conn = db.connect(path)
    db.init_db(conn)
    bands, documents, _ = importer.read_sheet(sample_xlsx)
    importer.load(conn, bands, documents)
    conn.execute("INSERT INTO users (email, password_hash) VALUES (?, ?)",
                 (EMAIL, auth.hash_password(PASSWORD)))
    conn.commit()
    conn.close()
    return path


@pytest.fixture
def anon(db_path):
    """A client that hasn't signed in."""
    return TestClient(create_app(db_path))


@pytest.fixture
def client(anon):
    """A signed-in client (the session cookie is kept between requests)."""
    assert anon.post("/api/login", json={"email": EMAIL, "password": PASSWORD}).status_code == 200
    return anon
