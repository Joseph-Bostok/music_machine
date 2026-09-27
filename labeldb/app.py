"""Web service: JSON API plus the browser GUI.

Run with:
    uvicorn labeldb.app:app --reload
then open http://127.0.0.1:8000. Set LABEL_DB to use a database other
than ./label.db.
"""

import os
from pathlib import Path
from typing import Literal, Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from labeldb import db

STATIC_DIR = Path(__file__).with_name("static")

YesNo = Optional[Literal["yes", "no", "in_process", "onboarding"]]


class BandIn(BaseModel):
    name: str = Field(min_length=1)
    status: Literal["active", "onboarding", "inactive"] = "active"
    intern: Optional[str] = None
    sb_booking: YesNo = None
    sb_management: YesNo = None
    external_manager: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    private_parties: Optional[Literal["yes", "no", "possibly"]] = None
    notes: Optional[str] = None
    genres: list[str] = []


class LinkIn(BaseModel):
    kind: str = Field(min_length=1)
    # Only web URLs: links are rendered as <a href>, and a "javascript:"
    # URL there would run script when clicked.
    url: str = Field(pattern=r"^https?://\S+$")
    label: Optional[str] = None


class MemberIn(BaseModel):
    name: str = Field(min_length=1)
    role: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None


BAND_COLUMNS = [f for f in BandIn.model_fields if f != "genres"]


def create_app(db_path=None):
    conn = db.connect(db_path or os.environ.get("LABEL_DB", db.DEFAULT_DB_PATH))
    # Handlers are `async def` so they run one at a time on the event loop.
    # They share this single connection, and sync handlers would run
    # concurrently in a thread pool and could interleave transactions.
    db.init_db(conn)
    app = FastAPI(title="Label Roster")

    def get_band_or_404(band_id):
        band = conn.execute("SELECT * FROM bands WHERE id = ?", (band_id,)).fetchone()
        if band is None:
            raise HTTPException(404, "Band not found")
        return band

    def band_detail(band_id):
        band = dict(get_band_or_404(band_id))
        band["genres"] = db.band_genres(conn, band_id)
        band["links"] = [dict(r) for r in conn.execute(
            "SELECT id, kind, url, label FROM links WHERE band_id = ? ORDER BY kind, id",
            (band_id,))]
        band["members"] = [dict(r) for r in conn.execute(
            """SELECT bm.id, p.id AS person_id, p.name, bm.role, p.email, p.phone
               FROM band_members bm JOIN people p ON p.id = bm.person_id
               WHERE bm.band_id = ? ORDER BY p.name""", (band_id,))]
        # Other bands each member plays in: the payoff of storing people once.
        for m in band["members"]:
            m["other_bands"] = [r["name"] for r in conn.execute(
                """SELECT b.name FROM band_members bm JOIN bands b ON b.id = bm.band_id
                   WHERE bm.person_id = ? AND bm.band_id != ?""", (m["person_id"], band_id))]
        return band

    @app.get("/", include_in_schema=False)
    async def index():
        return FileResponse(STATIC_DIR / "index.html")

    @app.get("/api/bands")
    async def list_bands(
        q: str = "",
        status: Optional[str] = None,
        genre: Optional[str] = None,
        state: Optional[str] = None,
        intern: Optional[str] = None,
        missing: Optional[str] = Query(None, description="link kind the band lacks, e.g. epk"),
    ):
        # Build the WHERE clause from whichever filters are set. Values are
        # always passed as parameters, never formatted into the SQL string.
        where, params = [], []
        if q.strip():
            match = db.fts_query(q)
            if match:
                where.append("b.id IN (SELECT band_id FROM band_search WHERE band_search MATCH ?)")
                params.append(match)
        if status:
            where.append("b.status = ?"); params.append(status)
        if state:
            where.append("b.state = ?"); params.append(state)
        if intern:
            where.append("b.intern = ? COLLATE NOCASE"); params.append(intern)
        if genre:
            where.append("""b.id IN (SELECT bg.band_id FROM band_genres bg
                            JOIN genres g ON g.id = bg.genre_id WHERE g.name = ?)""")
            params.append(genre)
        if missing:
            where.append("b.id NOT IN (SELECT band_id FROM links WHERE kind = ?)")
            params.append(missing)
        sql = """SELECT b.id, b.name, b.status, b.intern, b.city, b.state,
                        b.sb_booking, b.sb_management,
                        (SELECT group_concat(g.name, ' / ') FROM band_genres bg
                         JOIN genres g ON g.id = bg.genre_id WHERE bg.band_id = b.id) AS genres
                 FROM bands b"""
        if where:
            sql += " WHERE " + " AND ".join(where)
        sql += " ORDER BY b.name COLLATE NOCASE"
        return [dict(r) for r in conn.execute(sql, params)]

    @app.get("/api/bands/{band_id}")
    async def get_band(band_id: int):
        return band_detail(band_id)

    @app.post("/api/bands", status_code=201)
    async def create_band(band: BandIn):
        data = band.model_dump()
        try:
            band_id = conn.execute(
                f"INSERT INTO bands ({', '.join(BAND_COLUMNS)}) "
                f"VALUES ({', '.join(':' + c for c in BAND_COLUMNS)})", data).lastrowid
        except db.sqlite3.IntegrityError:
            conn.rollback()
            raise HTTPException(409, f"A band named {band.name!r} already exists")
        db.set_genres(conn, band_id, band.genres)
        db.reindex_band(conn, band_id)
        conn.commit()
        return band_detail(band_id)

    @app.put("/api/bands/{band_id}")
    async def update_band(band_id: int, band: BandIn):
        get_band_or_404(band_id)
        data = band.model_dump() | {"id": band_id}
        try:
            conn.execute(
                f"UPDATE bands SET {', '.join(f'{c} = :{c}' for c in BAND_COLUMNS)} WHERE id = :id",
                data)
        except db.sqlite3.IntegrityError:
            conn.rollback()
            raise HTTPException(409, f"A band named {band.name!r} already exists")
        db.set_genres(conn, band_id, band.genres)
        db.reindex_band(conn, band_id)
        conn.commit()
        return band_detail(band_id)

    @app.delete("/api/bands/{band_id}", status_code=204)
    async def delete_band(band_id: int):
        get_band_or_404(band_id)
        conn.execute("DELETE FROM bands WHERE id = ?", (band_id,))
        conn.execute("DELETE FROM band_search WHERE band_id = ?", (band_id,))
        conn.execute("DELETE FROM genres WHERE id NOT IN (SELECT genre_id FROM band_genres)")
        conn.commit()

    @app.post("/api/bands/{band_id}/links", status_code=201)
    async def add_link(band_id: int, link: LinkIn):
        get_band_or_404(band_id)
        conn.execute("INSERT INTO links (band_id, kind, url, label) VALUES (?, ?, ?, ?)",
                     (band_id, link.kind, link.url, link.label))
        conn.commit()
        return band_detail(band_id)

    @app.delete("/api/links/{link_id}", status_code=204)
    async def delete_link(link_id: int):
        if conn.execute("DELETE FROM links WHERE id = ?", (link_id,)).rowcount == 0:
            raise HTTPException(404, "Link not found")
        conn.commit()

    @app.post("/api/bands/{band_id}/members", status_code=201)
    async def add_member(band_id: int, member: MemberIn):
        get_band_or_404(band_id)
        try:
            db.add_member(conn, band_id, member.name, member.role, member.email, member.phone)
        except db.sqlite3.IntegrityError:
            conn.rollback()
            raise HTTPException(409, f"{member.name} is already a member")
        db.reindex_band(conn, band_id)
        conn.commit()
        return band_detail(band_id)

    @app.delete("/api/members/{member_id}", status_code=204)
    async def remove_member(member_id: int):
        row = conn.execute("SELECT band_id FROM band_members WHERE id = ?", (member_id,)).fetchone()
        if row is None:
            raise HTTPException(404, "Member not found")
        conn.execute("DELETE FROM band_members WHERE id = ?", (member_id,))
        db.reindex_band(conn, row["band_id"])
        conn.commit()

    @app.get("/api/documents")
    async def list_documents():
        return [dict(r) for r in conn.execute("SELECT * FROM documents ORDER BY title")]

    @app.get("/api/facets")
    async def facets():
        """Distinct values for the GUI's filter dropdowns."""
        def distinct(sql):
            return [r[0] for r in conn.execute(sql)]
        return {
            "genres": distinct("SELECT name FROM genres ORDER BY name"),
            "states": distinct("SELECT DISTINCT state FROM bands WHERE state IS NOT NULL ORDER BY state"),
            "interns": distinct("SELECT DISTINCT intern FROM bands WHERE intern IS NOT NULL ORDER BY intern"),
            "link_kinds": distinct("SELECT DISTINCT kind FROM links ORDER BY kind"),
        }

    return app


app = create_app()
