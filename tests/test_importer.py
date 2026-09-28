from labeldb import db, importer


def test_parse_helpers():
    assert importer.parse_name("Whisper Doll - onboarding") == ("Whisper Doll", "onboarding")
    assert importer.parse_name("Don't Tell Iris") == ("Don't Tell Iris", "active")
    assert importer.parse_yes_no("no - Jon Borneman", {"yes", "no"}) == ("no", "Jon Borneman")
    assert importer.parse_yes_no("in process", {"in_process"}) == ("in_process", None)
    assert importer.parse_yes_no("maybe", {"yes", "no"}) == (None, "maybe")
    assert importer.parse_location("Charlotte - NC") == ("Charlotte", "NC", True)
    assert importer.parse_location("NYC") == ("NYC", "NY", True)
    assert importer.parse_location("Atlanta - Charleston") == ("Atlanta - Charleston", None, False)
    assert importer.parse_genres(" Rock / Alt / rock ") == ["Rock", "Alternative"]


def test_read_sheet(sample_xlsx):
    bands, documents, issues = importer.read_sheet(sample_xlsx)
    by_name = {b["name"]: b for b in bands}
    assert set(by_name) == {"Auroras Hope", "Bedrumor", "Current Blue"}

    auroras = by_name["Auroras Hope"]
    assert auroras["intern"] is None  # "n/a"
    assert auroras["genres"] == ["Grunge", "Rock"]
    assert {l["kind"] for l in auroras["links"]} == {"band_sheet", "artist_alignment", "epk"}

    bedrumor = by_name["Bedrumor"]
    assert bedrumor["status"] == "onboarding"
    assert (bedrumor["sb_management"], bedrumor["external_manager"]) == ("no", "Jon Borneman")
    assert bedrumor["private_parties"] == "possibly"

    assert by_name["Current Blue"]["sb_booking"] == "in_process"

    # Documents come from the docs columns on any row, including rows with no band.
    assert [d["title"] for d in documents] == ["Playlists research", "Venues", "Music Festivals"]

    joined = "\n".join(issues)
    assert "L2: document entry '2025'" in joined
    assert "H3 (Bedrumor): says 'Drive Link' but has no link" in joined
    assert "Atlanta - Charleston" in joined
    assert "Same link used for Auroras Hope, Current Blue" in joined


def test_import_cli_refuses_to_overwrite(sample_xlsx, tmp_path, capsys):
    path = tmp_path / "label.db"
    importer.main([str(sample_xlsx), "--db", str(path)])
    try:
        importer.main([str(sample_xlsx), "--db", str(path)])
        raise AssertionError("second import should refuse without --replace")
    except SystemExit as e:
        assert "--replace" in str(e)
    importer.main([str(sample_xlsx), "--db", str(path), "--replace"])
    conn = db.connect(path)
    assert conn.execute("SELECT COUNT(*) FROM bands").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM band_search").fetchone()[0] == 3


def test_sql_export_round_trips(sample_xlsx, tmp_path):
    """The --sql output (for Cloudflare D1) loads into an empty schema and
    produces the same data and search index as a direct import."""
    out = tmp_path / "roster.sql"
    importer.main([str(sample_xlsx), "--sql", str(out)])
    sql = out.read_text()
    assert "BEGIN" not in sql and "COMMIT" not in sql  # D1 rejects explicit transactions

    conn = db.connect(tmp_path / "fresh.db")
    db.init_db(conn)
    conn.executescript(sql)
    conn.executescript(sql)  # re-running replaces rather than duplicates
    assert conn.execute("SELECT COUNT(*) FROM bands").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM documents").fetchone()[0] == 3
    assert conn.execute("SELECT COUNT(*) FROM band_search").fetchone()[0] == 3
    hits = conn.execute(
        "SELECT b.name FROM band_search s JOIN bands b ON b.id = s.band_id WHERE band_search MATCH ?",
        (db.fts_query("borne"),)).fetchall()
    assert [h["name"] for h in hits] == ["Bedrumor"]
