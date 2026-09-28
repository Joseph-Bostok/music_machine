# Deploying to Cloudflare (no server to run)

Cloudflare runs everything, so there's no machine for you to maintain:

```
browser ──▶ Cloudflare Access ──(signed-in only)──▶ Worker (cloudflare/src/index.ts) ──▶ D1 database
            email code or Google                    same API + GUI as the Python app      (SQLite)
```

| Piece | What it is | Free plan covers |
|---|---|---|
| **Worker** | Runs the API and serves the GUI | 100k requests/day |
| **D1** | Cloudflare's hosted SQLite, so the roster is still a relational database | 5 GB, millions of row reads/day |
| **Access** | Sign-in in front of the app; only emails you list get in | Up to 50 users |

(These limits are from memory; check Cloudflare's pricing pages for the current numbers.)

Setup takes about 20 minutes. The account should belong to **the label owner**, so the label owns its
data. Add yourself under *Manage Account → Members*.

---

## 0. What you need

- A free Cloudflare account: https://dash.cloudflare.com/sign-up
- Node.js 20+ on your Mac: `brew install node`
- This repo, and the Python setup from the README (only needed for the import step)

```bash
cd cloudflare
npm install
npx wrangler login        # opens the browser; approve access to the Cloudflare account
```

## 1. Create the database

```bash
npx wrangler d1 create label-roster
```

It prints a `database_id`. Paste it into `cloudflare/wrangler.jsonc`, replacing the
`00000000-...` placeholder (newer Wrangler versions may offer to do this for you). Then create the tables:

```bash
npm run db:migrate
```

## 2. Import the spreadsheet

Run these one at a time from the **repo root**:

```bash
python -m labeldb.importer "SB Master File.xlsx" --sql cloudflare/roster.sql
cd cloudflare
npx wrangler d1 execute label-roster --remote --file roster.sql
rm roster.sql
```

`roster.sql` contains the label's data. Git is set to ignore it, but delete it once it's uploaded anyway.

## 3. Deploy

```bash
npm run deploy
```

This prints a URL like `https://label-roster.<your-subdomain>.workers.dev`. If you open it now you'll
get `{"detail":"Not signed in"}`. **That's expected:** the app refuses everyone until Access is set up
(it "fails closed").

## 4. Put Cloudflare Access in front

1. In the dashboard, open **Zero Trust**. The first time, it asks you to pick a **team name**. This
   creates your *team domain*, `<team-name>.cloudflareaccess.com`. Choose the **Free** plan. It may
   ask for a card even though the plan is free.
2. **Access → Applications → Add an application → Self-hosted.**
   - **Application domain:** `label-roster.<your-subdomain>.workers.dev` (or your custom domain, see below)
   - **Session duration:** e.g. 1 week
3. **Add a policy:**
   - **Action:** Allow
   - **Include → Emails:** list each person's address, *or* **Emails ending in** `@yourlabel.com` if the label has its own Google Workspace domain
4. **Login methods:** "One-time PIN" works out of the box (Access emails a code). To use
   **Sign in with Google**, add Google under *Zero Trust → Settings → Authentication*.
5. Save, then open the application's settings and copy its **Application Audience (AUD) Tag**.

## 5. Tell the Worker which Access application to trust

In `cloudflare/wrangler.jsonc`, fill in the two values:

```jsonc
"vars": {
  "TEAM_DOMAIN": "<team-name>.cloudflareaccess.com",
  "POLICY_AUD": "<the AUD tag you copied>"
}
```

Neither value is secret. Then redeploy:

```bash
npm run deploy
```

**Why this step matters:** Access protects the address you configured, but a Worker can have more
than one address. The Worker checks the signed token Access attaches to each request, making sure
it's genuine, not expired, and issued for *this* application. A request that reached the Worker any
other way is refused.

## 6. Test it

Open the URL in a private window. You should see Cloudflare's sign-in page, get an email code, then
see the roster. Also check:
- An email that isn't on the list is refused.
- **Sign out** logs you out of Access.

Send your friend the link. That's it.

---

## Day-to-day

| Task | How |
|---|---|
| Add or remove a person | Edit the Access application's policy (step 4.3) |
| Offsite backup | Click **Download backup** in the app. It saves the whole roster as a `.sql` file. |
| Restore a backup | `npx wrangler d1 execute label-roster --remote --file roster-backup-YYYY-MM-DD.sql` (replaces all roster data) |
| Undo a recent mistake | D1 **Time Travel**: `npx wrangler d1 time-travel info label-roster`, then `npx wrangler d1 time-travel restore label-roster --timestamp=<ISO time>` (the free plan keeps a shorter history than paid) |
| Deploy code changes | `git pull`, then `npm run deploy` in `cloudflare/` |
| Change the schema | Add `cloudflare/migrations/0002_*.sql`, then `npm run db:migrate` |
| See errors | Dashboard → Workers & Pages → label-roster → Logs, or `npx wrangler tail` |

## Optional: your own domain

If the domain's DNS is on Cloudflare:
1. **Workers & Pages → label-roster → Settings → Domains & Routes → Add → Custom domain**, e.g. `roster.yourlabel.com`.
2. Add the same hostname to the Access application.

## Working on it locally

```bash
cd cloudflare
echo "DEV_USER_EMAIL=you@example.com" > .dev.vars   # bypasses Access, but only on localhost
npm run db:migrate:local
npx wrangler d1 execute label-roster --local --file roster.sql
npm run dev                                         # http://localhost:8787
```
