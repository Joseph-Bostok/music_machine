import pytest
from fastapi.testclient import TestClient

from labeldb import db, importer
from labeldb.app import create_app


@pytest.fixture
def client(sample_xlsx, tmp_path):
    path = tmp_path / "label.db"
    conn = db.connect(path)
    db.init_db(conn)
    bands, documents, _ = importer.read_sheet(sample_xlsx)
    importer.load(conn, bands, documents)
    conn.close()
    return TestClient(create_app(path))


def names(resp):
    return [b["name"] for b in resp.json()]


def test_search_prefix_and_filters(client):
    assert names(client.get("/api/bands", params={"q": "aur"})) == ["Auroras Hope"]
    assert names(client.get("/api/bands", params={"q": "charlotte"})) == ["Auroras Hope"]
    assert names(client.get("/api/bands", params={"q": "borneman"})) == ["Bedrumor"]
    # Punctuation in the query must not break FTS5 syntax.
    assert client.get("/api/bands", params={"q": 'don"t -tell: ('}).status_code == 200
    assert names(client.get("/api/bands", params={"genre": "Alternative"})) == ["Bedrumor", "Current Blue"]
    assert names(client.get("/api/bands", params={"status": "onboarding"})) == ["Bedrumor"]
    assert names(client.get("/api/bands", params={"missing": "epk"})) == ["Bedrumor"]


def test_members_are_shared_and_searchable(client):
    ids = {b["name"]: b["id"] for b in client.get("/api/bands").json()}
    client.post(f"/api/bands/{ids['Auroras Hope']}/members", json={"name": "Sam Lee", "role": "drums"})
    detail = client.post(f"/api/bands/{ids['Current Blue']}/members",
                         json={"name": "sam lee", "role": "bass"}).json()
    # Same person (case-insensitive) is reused, so the app knows they play in both.
    assert detail["members"][0]["other_bands"] == ["Auroras Hope"]
    assert names(client.get("/api/bands", params={"q": "sam"})) == ["Auroras Hope", "Current Blue"]

    dup = client.post(f"/api/bands/{ids['Current Blue']}/members", json={"name": "Sam Lee"})
    assert dup.status_code == 409

    client.delete(f"/api/members/{detail['members'][0]['id']}")
    assert names(client.get("/api/bands", params={"q": "sam"})) == ["Auroras Hope"]


def test_create_update_delete_band(client):
    new = client.post("/api/bands", json={"name": "Yes Dear", "city": "Athens", "state": "GA",
                                          "genres": ["Indie Rock"]})
    assert new.status_code == 201
    band_id = new.json()["id"]
    assert client.post("/api/bands", json={"name": "yes dear"}).status_code == 409

    updated = client.put(f"/api/bands/{band_id}", json={"name": "Yes Dear", "genres": ["Punk"],
                                                        "status": "inactive"}).json()
    assert updated["genres"] == ["Punk"]
    assert names(client.get("/api/bands", params={"q": "athens"})) == []  # city cleared by PUT
    assert "Indie Rock" not in client.get("/api/facets").json()["genres"]  # orphan genre removed

    assert client.delete(f"/api/bands/{band_id}").status_code == 204
    assert client.get(f"/api/bands/{band_id}").status_code == 404
    assert names(client.get("/api/bands", params={"q": "yes"})) == []


def test_links_reject_non_web_urls(client):
    band_id = client.get("/api/bands").json()[0]["id"]
    bad = client.post(f"/api/bands/{band_id}/links", json={"kind": "epk", "url": "javascript:alert(1)"})
    assert bad.status_code == 422
    ok = client.post(f"/api/bands/{band_id}/links",
                     json={"kind": "instagram", "url": "https://instagram.com/aurorashope"})
    assert any(l["kind"] == "instagram" for l in ok.json()["links"])


def test_documents(client):
    titles = [d["title"] for d in client.get("/api/documents").json()]
    assert titles == ["Music Festivals", "Playlists research", "Venues"]
