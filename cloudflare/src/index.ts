/**
 * Label Roster on Cloudflare: the same JSON API as labeldb/app.py, running
 * as a Worker on a D1 (SQLite) database, behind Cloudflare Access.
 *
 * Sign-in is done by Cloudflare Access before a request reaches this code.
 * Access adds a signed JWT (the Cf-Access-Jwt-Assertion header) to every
 * request it lets through, and we verify that JWT here too. That second
 * check matters: without it, anyone who found the Worker's other URLs
 * (e.g. *.workers.dev) could skip Access entirely.
 */

import { Hono } from "hono";
import type { Context } from "hono";
import { createRemoteJWKSet, jwtVerify } from "jose";

type Env = {
  DB: D1Database;
  ASSETS: Fetcher;
  TEAM_DOMAIN?: string;   // e.g. "yourteam.cloudflareaccess.com"
  POLICY_AUD?: string;    // the Access application's "Application Audience (AUD) Tag"
  DEV_USER_EMAIL?: string; // local development only; ignored unless on localhost
};

type Vars = { email: string };
type Ctx = Context<{ Bindings: Env; Variables: Vars }>;

const app = new Hono<{ Bindings: Env; Variables: Vars }>();

class HttpError extends Error {
  constructor(public status: number, message: string) {
    super(message);
  }
}

// --- authentication ---------------------------------------------------------

// Access's public signing keys, fetched once per Worker instance and cached.
const jwksCache = new Map<string, ReturnType<typeof createRemoteJWKSet>>();

async function accessEmail(req: Request, env: Env): Promise<string | null> {
  const host = new URL(req.url).hostname;
  if (env.DEV_USER_EMAIL && (host === "localhost" || host === "127.0.0.1")) {
    return env.DEV_USER_EMAIL;
  }
  const token = req.headers.get("Cf-Access-Jwt-Assertion");
  // Fail closed: if Access isn't configured, nobody gets in.
  if (!token || !env.TEAM_DOMAIN || !env.POLICY_AUD) return null;

  const team = "https://" + env.TEAM_DOMAIN.replace(/^https?:\/\//, "").replace(/\/+$/, "");
  let jwks = jwksCache.get(team);
  if (!jwks) {
    jwks = createRemoteJWKSet(new URL(`${team}/cdn-cgi/access/certs`));
    jwksCache.set(team, jwks);
  }
  try {
    // Checks the signature, expiry, issuer (our Access team) and audience
    // (this specific application), so a token for another app is rejected.
    const { payload } = await jwtVerify(token, jwks, { issuer: team, audience: env.POLICY_AUD });
    return typeof payload.email === "string" ? payload.email : null;
  } catch {
    return null;
  }
}

app.use("*", async (c, next) => {
  const email = await accessEmail(c.req.raw, c.env);
  if (!email) return c.json({ detail: "Not signed in" }, 403);
  c.set("email", email);
  await next();
  // Never let the browser cache pages or data (see app.py for the reasoning).
  // Responses from the ASSETS binding have immutable headers, so copy first.
  c.res = new Response(c.res.body, c.res);
  c.res.headers.set("Cache-Control", "no-store");
});

app.onError((err, c) => {
  if (err instanceof HttpError) return c.json({ detail: err.message }, err.status as 400);
  console.error(err);
  return c.json({ detail: "Internal error" }, 500);
});

// --- validation (mirrors the pydantic models in app.py) ----------------------

const YES_NO = ["yes", "no", "in_process", "onboarding"];
const BAND_FIELDS = ["name", "status", "intern", "sb_booking", "sb_management",
  "external_manager", "city", "state", "private_parties", "notes"] as const;

type Band = Record<(typeof BAND_FIELDS)[number], string | null> & { genres: string[] };

async function body(c: Ctx): Promise<Record<string, unknown>> {
  try {
    const data = await c.req.json();
    if (data && typeof data === "object" && !Array.isArray(data)) return data;
  } catch { /* fall through */ }
  throw new HttpError(422, "Request body must be a JSON object");
}

function optString(data: Record<string, unknown>, key: string): string | null {
  const v = data[key];
  if (v === undefined || v === null) return null;
  if (typeof v !== "string") throw new HttpError(422, `${key} must be a string`);
  return v;
}

function requiredString(data: Record<string, unknown>, key: string): string {
  const v = optString(data, key);
  if (!v || !v.trim()) throw new HttpError(422, `${key} is required`);
  return v;
}

function optEnum(data: Record<string, unknown>, key: string, allowed: string[]): string | null {
  const v = optString(data, key);
  if (v !== null && !allowed.includes(v)) throw new HttpError(422, `${key} must be one of ${allowed.join(", ")}`);
  return v;
}

function parseBand(data: Record<string, unknown>): Band {
  const genres = data.genres ?? [];
  if (!Array.isArray(genres) || !genres.every((g) => typeof g === "string")) {
    throw new HttpError(422, "genres must be a list of strings");
  }
  return {
    name: requiredString(data, "name"),
    status: optEnum(data, "status", ["active", "onboarding", "inactive"]) ?? "active",
    intern: optString(data, "intern"),
    sb_booking: optEnum(data, "sb_booking", YES_NO),
    sb_management: optEnum(data, "sb_management", YES_NO),
    external_manager: optString(data, "external_manager"),
    city: optString(data, "city"),
    state: optString(data, "state"),
    private_parties: optEnum(data, "private_parties", ["yes", "no", "possibly"]),
    notes: optString(data, "notes"),
    genres: genres.map((g: string) => g.trim()).filter(Boolean),
  };
}

function isUniqueViolation(err: unknown): boolean {
  return String((err as Error)?.message ?? err).includes("UNIQUE constraint failed");
}

// --- shared queries ---------------------------------------------------------

/** Builds full-text rows from current data, entirely in SQL. Kept identical
 * to REINDEX_ALL_SQL in labeldb/importer.py. */
const REINDEX_SELECT = `INSERT INTO band_search (band_id, content)
SELECT b.id,
       coalesce(b.name, '') || ' ' || coalesce(b.intern, '') || ' ' ||
       coalesce(b.external_manager, '') || ' ' || coalesce(b.city, '') || ' ' ||
       coalesce(b.state, '') || ' ' || coalesce(b.status, '') || ' ' ||
       coalesce(b.notes, '') || ' ' ||
       coalesce((SELECT group_concat(g.name, ' ') FROM band_genres bg
                 JOIN genres g ON g.id = bg.genre_id WHERE bg.band_id = b.id), '') || ' ' ||
       coalesce((SELECT group_concat(p.name || ' ' || coalesce(bm.role, ''), ' ')
                 FROM band_members bm JOIN people p ON p.id = bm.person_id
                 WHERE bm.band_id = b.id), '')
FROM bands b`;

/** Statements that rebuild one band's search row. Because it's plain SQL,
 * it runs in the same atomic batch as the change that made the row stale. */
function reindexStatements(db: D1Database, bandId: number): D1PreparedStatement[] {
  return [
    db.prepare("DELETE FROM band_search WHERE band_id = ?1").bind(bandId),
    db.prepare(`${REINDEX_SELECT} WHERE b.id = ?1`).bind(bandId),
  ];
}

function genreStatements(db: D1Database, bandId: number, genres: string[]): D1PreparedStatement[] {
  const stmts = [db.prepare("DELETE FROM band_genres WHERE band_id = ?").bind(bandId)];
  for (const name of genres) {
    stmts.push(db.prepare("INSERT OR IGNORE INTO genres (name) VALUES (?)").bind(name));
    stmts.push(db.prepare(
      "INSERT OR IGNORE INTO band_genres (band_id, genre_id) SELECT ?, id FROM genres WHERE name = ?",
    ).bind(bandId, name));
  }
  // Drop genres no band uses any more so filter dropdowns stay clean.
  stmts.push(db.prepare("DELETE FROM genres WHERE id NOT IN (SELECT genre_id FROM band_genres)"));
  return stmts;
}

async function requireBand(db: D1Database, id: number) {
  const band = await db.prepare("SELECT * FROM bands WHERE id = ?").bind(id).first();
  if (!band) throw new HttpError(404, "Band not found");
  return band;
}

async function bandDetail(db: D1Database, id: number) {
  const band = await requireBand(db, id);
  const [genres, links, members, others] = await db.batch([
    db.prepare(`SELECT g.name FROM genres g JOIN band_genres bg ON bg.genre_id = g.id
                WHERE bg.band_id = ? ORDER BY g.name`).bind(id),
    db.prepare("SELECT id, kind, url, label FROM links WHERE band_id = ? ORDER BY kind, id").bind(id),
    db.prepare(`SELECT bm.id, p.id AS person_id, p.name, bm.role, p.email, p.phone
                FROM band_members bm JOIN people p ON p.id = bm.person_id
                WHERE bm.band_id = ? ORDER BY p.name`).bind(id),
    // Other bands each member plays in: the payoff of storing people once.
    db.prepare(`SELECT bm.person_id, b.name FROM band_members bm JOIN bands b ON b.id = bm.band_id
                WHERE bm.band_id != ?1 AND bm.person_id IN
                  (SELECT person_id FROM band_members WHERE band_id = ?1)`).bind(id),
  ]);
  const otherBands = new Map<number, string[]>();
  for (const row of others.results as { person_id: number; name: string }[]) {
    otherBands.set(row.person_id, [...(otherBands.get(row.person_id) ?? []), row.name]);
  }
  return {
    ...band,
    genres: (genres.results as { name: string }[]).map((g) => g.name),
    links: links.results,
    members: (members.results as { person_id: number }[]).map((m) => ({
      ...m, other_bands: otherBands.get(m.person_id) ?? [],
    })),
  };
}

/** "celler dw" -> '"celler"* "dw"*': prefix matching, with each word quoted
 * so punctuation can't be parsed as FTS5 syntax. */
function ftsQuery(text: string): string {
  return (text.match(/[\p{L}\p{N}_]+/gu) ?? []).map((w) => `"${w}"*`).join(" ");
}

function intParam(c: Ctx, name: string): number {
  const n = Number(c.req.param(name));
  if (!Number.isInteger(n)) throw new HttpError(422, `${name} must be an integer`);
  return n;
}

// --- routes -----------------------------------------------------------------

app.get("/api/me", (c) =>
  c.json({ email: c.get("email"), logout_url: "/cdn-cgi/access/logout", backup_url: "/api/export" }));

app.get("/api/bands", async (c) => {
  const q = c.req.query();
  const where: string[] = [];
  const params: unknown[] = [];
  const match = ftsQuery(q.q ?? "");
  if (match) {
    where.push("b.id IN (SELECT band_id FROM band_search WHERE band_search MATCH ?)");
    params.push(match);
  }
  if (q.status) { where.push("b.status = ?"); params.push(q.status); }
  if (q.state) { where.push("b.state = ?"); params.push(q.state); }
  if (q.intern) { where.push("b.intern = ? COLLATE NOCASE"); params.push(q.intern); }
  if (q.genre) {
    where.push(`b.id IN (SELECT bg.band_id FROM band_genres bg
                JOIN genres g ON g.id = bg.genre_id WHERE g.name = ?)`);
    params.push(q.genre);
  }
  if (q.missing) {
    where.push("b.id NOT IN (SELECT band_id FROM links WHERE kind = ?)");
    params.push(q.missing);
  }
  let sql = `SELECT b.id, b.name, b.status, b.intern, b.city, b.state, b.sb_booking, b.sb_management,
               (SELECT group_concat(g.name, ' / ') FROM band_genres bg
                JOIN genres g ON g.id = bg.genre_id WHERE bg.band_id = b.id) AS genres
             FROM bands b`;
  if (where.length) sql += " WHERE " + where.join(" AND ");
  sql += " ORDER BY b.name COLLATE NOCASE";
  const { results } = await c.env.DB.prepare(sql).bind(...params).all();
  return c.json(results);
});

app.get("/api/bands/:id", async (c) => c.json(await bandDetail(c.env.DB, intParam(c, "id"))));

app.post("/api/bands", async (c) => {
  const db = c.env.DB;
  const band = parseBand(await body(c));
  let id: number;
  try {
    const row = await db.prepare(
      `INSERT INTO bands (${BAND_FIELDS.join(", ")}) VALUES (${BAND_FIELDS.map(() => "?").join(", ")})
       RETURNING id`,
    ).bind(...BAND_FIELDS.map((f) => band[f])).first<{ id: number }>();
    id = row!.id;
  } catch (err) {
    if (isUniqueViolation(err)) throw new HttpError(409, `A band named '${band.name}' already exists`);
    throw err;
  }
  try {
    await db.batch([...genreStatements(db, id, band.genres), ...reindexStatements(db, id)]);
  } catch (err) {
    // Don't leave a half-created band behind.
    await db.prepare("DELETE FROM bands WHERE id = ?").bind(id).run();
    throw err;
  }
  return c.json(await bandDetail(db, id), 201);
});

app.put("/api/bands/:id", async (c) => {
  const db = c.env.DB;
  const id = intParam(c, "id");
  await requireBand(db, id);
  const band = parseBand(await body(c));
  try {
    // One batch = one transaction: the update, genres and search index
    // change together or not at all.
    await db.batch([
      db.prepare(`UPDATE bands SET ${BAND_FIELDS.map((f) => `${f} = ?`).join(", ")} WHERE id = ?`)
        .bind(...BAND_FIELDS.map((f) => band[f]), id),
      ...genreStatements(db, id, band.genres),
      ...reindexStatements(db, id),
    ]);
  } catch (err) {
    if (isUniqueViolation(err)) throw new HttpError(409, `A band named '${band.name}' already exists`);
    throw err;
  }
  return c.json(await bandDetail(db, id));
});

app.delete("/api/bands/:id", async (c) => {
  const db = c.env.DB;
  const id = intParam(c, "id");
  await requireBand(db, id);
  await db.batch([
    db.prepare("DELETE FROM bands WHERE id = ?").bind(id),
    db.prepare("DELETE FROM band_search WHERE band_id = ?").bind(id),
    db.prepare("DELETE FROM genres WHERE id NOT IN (SELECT genre_id FROM band_genres)"),
  ]);
  return c.body(null, 204);
});

app.post("/api/bands/:id/links", async (c) => {
  const db = c.env.DB;
  const id = intParam(c, "id");
  await requireBand(db, id);
  const data = await body(c);
  const kind = requiredString(data, "kind");
  const url = requiredString(data, "url");
  // Only web URLs: links are rendered as <a href>, and a "javascript:" URL
  // there would run script when clicked.
  if (!/^https?:\/\/\S+$/.test(url)) throw new HttpError(422, "url must start with http:// or https://");
  await db.prepare("INSERT INTO links (band_id, kind, url, label) VALUES (?, ?, ?, ?)")
    .bind(id, kind, url, optString(data, "label")).run();
  return c.json(await bandDetail(db, id), 201);
});

app.delete("/api/links/:id", async (c) => {
  const res = await c.env.DB.prepare("DELETE FROM links WHERE id = ?").bind(intParam(c, "id")).run();
  if (!res.meta.changes) throw new HttpError(404, "Link not found");
  return c.body(null, 204);
});

app.post("/api/bands/:id/members", async (c) => {
  const db = c.env.DB;
  const id = intParam(c, "id");
  await requireBand(db, id);
  const data = await body(c);
  const name = requiredString(data, "name").trim();
  const [role, email, phone] = ["role", "email", "phone"].map((k) => optString(data, k));
  try {
    // Reuse an existing person with the same name (case-insensitive), so
    // someone in two bands is one row. All in one atomic batch.
    await db.batch([
      db.prepare(`INSERT INTO people (name, email, phone) SELECT ?1, ?2, ?3
                  WHERE NOT EXISTS (SELECT 1 FROM people WHERE name = ?1 COLLATE NOCASE)`)
        .bind(name, email, phone),
      db.prepare(`UPDATE people SET email = coalesce(?2, email), phone = coalesce(?3, phone)
                  WHERE name = ?1 COLLATE NOCASE`).bind(name, email, phone),
      db.prepare(`INSERT INTO band_members (band_id, person_id, role)
                  SELECT ?1, id, ?2 FROM people WHERE name = ?3 COLLATE NOCASE ORDER BY id LIMIT 1`)
        .bind(id, role, name),
      ...reindexStatements(db, id),
    ]);
  } catch (err) {
    if (isUniqueViolation(err)) throw new HttpError(409, `${name} is already a member`);
    throw err;
  }
  return c.json(await bandDetail(db, id), 201);
});

app.delete("/api/members/:id", async (c) => {
  const db = c.env.DB;
  const row = await db.prepare("SELECT band_id FROM band_members WHERE id = ?")
    .bind(intParam(c, "id")).first<{ band_id: number }>();
  if (!row) throw new HttpError(404, "Member not found");
  await db.batch([
    db.prepare("DELETE FROM band_members WHERE id = ?").bind(intParam(c, "id")),
    ...reindexStatements(db, row.band_id),
  ]);
  return c.body(null, 204);
});

app.get("/api/documents", async (c) => {
  const { results } = await c.env.DB.prepare("SELECT * FROM documents ORDER BY title").all();
  return c.json(results);
});

app.get("/api/facets", async (c) => {
  const db = c.env.DB;
  const col = (r: D1Result) => (r.results as Record<string, unknown>[]).map((row) => Object.values(row)[0]);
  const [genres, states, interns, kinds] = await db.batch([
    db.prepare("SELECT name FROM genres ORDER BY name"),
    db.prepare("SELECT DISTINCT state FROM bands WHERE state IS NOT NULL ORDER BY state"),
    db.prepare("SELECT DISTINCT intern FROM bands WHERE intern IS NOT NULL ORDER BY intern"),
    db.prepare("SELECT DISTINCT kind FROM links ORDER BY kind"),
  ]);
  return c.json({ genres: col(genres), states: col(states), interns: col(interns), link_kinds: col(kinds) });
});

/** Full roster as SQL, in the same format as `labeldb.importer --sql`, so a
 * download can be restored with `wrangler d1 execute --file`. */
const ROSTER_TABLES = ["bands", "genres", "band_genres", "people", "band_members", "links", "documents"];

app.get("/api/export", async (c) => {
  const db = c.env.DB;
  const lines = [
    `-- Label Roster backup, ${new Date().toISOString()}. Replaces all roster data.`,
    "DELETE FROM band_search;",
    ...[...ROSTER_TABLES].reverse().map((t) => `DELETE FROM ${t};`),
  ];
  for (const table of ROSTER_TABLES) {
    const cols = ((await db.prepare(`PRAGMA table_info(${table})`).all()).results as { name: string }[])
      .map((r) => r.name);
    // quote() makes SQLite itself escape every value as a SQL literal.
    const { results } = await db.prepare(
      `SELECT ${cols.map((col) => `quote(${col})`).join(" || ', ' || ")} AS v FROM ${table}`,
    ).all<{ v: string }>();
    for (const row of results) lines.push(`INSERT INTO ${table} (${cols.join(", ")}) VALUES (${row.v});`);
  }
  lines.push(`${REINDEX_SELECT};`);
  const date = new Date().toISOString().slice(0, 10);
  return new Response(lines.join("\n") + "\n", {
    headers: {
      "Content-Type": "application/sql; charset=utf-8",
      "Content-Disposition": `attachment; filename="roster-backup-${date}.sql"`,
    },
  });
});

app.all("/api/*", (c) => c.json({ detail: "Not found" }, 404));

// Everything else is the GUI: static files from labeldb/static.
app.all("*", (c) => c.env.ASSETS.fetch(c.req.raw));

export default app;
