import pytest

from labeldb import auth, backup, db, users
from conftest import EMAIL, PASSWORD


def test_password_hashing():
    stored = auth.hash_password("hunter2hunter2")
    assert stored.startswith("scrypt$") and "hunter2" not in stored
    assert auth.verify_password("hunter2hunter2", stored)
    assert not auth.verify_password("hunter2hunter3", stored)
    assert not auth.verify_password("x", "garbage")
    # Same password, different salt -> different hash.
    assert auth.hash_password("hunter2hunter2") != stored


@pytest.mark.parametrize("path", ["/api/bands", "/api/bands/1", "/api/documents",
                                  "/api/facets", "/api/me"])
def test_api_requires_login(anon, path):
    assert anon.get(path).status_code == 401


def test_pages_redirect_to_login(anon):
    for path in ("/", "/docs", "/openapi.json"):
        r = anon.get(path, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == "/login", path
    assert anon.get("/login").status_code == 200


def test_writes_require_login(anon):
    assert anon.post("/api/bands", json={"name": "Sneaky"}).status_code == 401
    assert anon.delete("/api/bands/1").status_code == 401


def test_login_logout_cycle(anon):
    bad = anon.post("/api/login", json={"email": EMAIL, "password": "wrong password"})
    assert bad.status_code == 401
    unknown = anon.post("/api/login", json={"email": "nobody@example.com", "password": PASSWORD})
    assert unknown.json() == bad.json()  # same message: doesn't reveal which emails exist

    ok = anon.post("/api/login", json={"email": EMAIL.upper(), "password": PASSWORD})
    assert ok.status_code == 200
    cookie = ok.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert anon.get("/api/me").json() == {"email": EMAIL}

    assert anon.post("/api/logout").status_code == 204
    assert anon.get("/api/me").status_code == 401


def test_responses_are_not_cacheable(client):
    for path in ("/", "/api/bands", "/login"):
        assert client.get(path).headers["cache-control"] == "no-store", path


def test_cookie_is_secure_over_https(db_path):
    from fastapi.testclient import TestClient
    from labeldb.app import create_app
    https = TestClient(create_app(db_path), base_url="https://testserver")
    r = https.post("/api/login", json={"email": EMAIL, "password": PASSWORD})
    assert "secure" in r.headers["set-cookie"].lower()


def test_session_token_not_stored_in_plaintext(anon, db_path):
    token = anon.post("/api/login", json={"email": EMAIL, "password": PASSWORD}).cookies["session"]
    stored = [r[0] for r in db.connect(db_path).execute("SELECT token_hash FROM sessions")]
    assert token not in stored and len(stored) == 1


def test_rate_limit(anon):
    for _ in range(5):
        anon.post("/api/login", json={"email": EMAIL, "password": "wrong password"})
    # Even the right password is refused while blocked.
    r = anon.post("/api/login", json={"email": EMAIL, "password": PASSWORD})
    assert r.status_code == 429


def test_limiter_window_expires():
    now = [0.0]
    limiter = auth.LoginLimiter(limit=2, window=60, clock=lambda: now[0])
    limiter.record_failure("A@x.com"); limiter.record_failure("a@x.com")
    assert limiter.blocked("a@X.com")
    now[0] = 61
    assert not limiter.blocked("a@x.com")


def test_users_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("LABEL_DB", str(tmp_path / "u.db"))
    users.main(["add", "intern@example.com"], password_source=lambda: "first password")
    with pytest.raises(SystemExit):
        users.main(["add", "INTERN@example.com"], password_source=lambda: "x" * 12)
    conn = db.connect()
    uid = conn.execute("SELECT id FROM users").fetchone()["id"]
    auth.create_session(conn, uid)

    users.main(["passwd", "intern@example.com"], password_source=lambda: "second password")
    user = auth.authenticate(conn, "intern@example.com", "second password")
    assert user is not None
    assert conn.execute("SELECT COUNT(*) FROM sessions").fetchone()[0] == 0  # signed out

    users.main(["remove", "intern@example.com"])
    users.main(["list"])
    assert "intern@example.com" not in capsys.readouterr().out.splitlines()


def test_backup_snapshot_and_prune(db_path, tmp_path):
    dest = tmp_path / "backups"
    conn = db.connect(db_path)
    for i in range(3):
        (dest / f"label-2020010{i}-000000.db").parent.mkdir(exist_ok=True)
        (dest / f"label-2020010{i}-000000.db").write_bytes(b"old")
    path = backup.snapshot(conn, dest)
    kept = backup.prune(dest, keep=2)
    assert path in kept and len(list(dest.glob("label-*.db"))) == 2
    copy = db.sqlite3.connect(path)
    assert copy.execute("SELECT COUNT(*) FROM bands").fetchone()[0] == 3
