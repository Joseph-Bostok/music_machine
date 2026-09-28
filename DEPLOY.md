# Deploying to Google Cloud (free tier)

The end result is a private link like `https://roster.yourlabel.com`. Only
people you create accounts for can sign in, and everything runs on one
free-tier VM.

```
browser ──HTTPS──▶ Caddy (port 443, auto certificates)
                     │
                     ▼ localhost only
                   uvicorn + FastAPI (port 8000) ──▶ SQLite: /var/lib/labeldb/label.db
                                                       │ nightly
                                                       ▼
                                          /var/lib/labeldb/backups (+ optional Cloud Storage)
```

The Google Cloud project should belong to **the label owner's Google
account**, so the label owns its data. Add yourself as a collaborator under
IAM → Grant access (role: *Compute Admin*).

## 1. Create the project and guard against surprise bills

1. Go to https://console.cloud.google.com, create a project (e.g. `label-roster`), and enable billing.
   Google requires a card even for free-tier usage.
2. **Billing → Budgets & alerts → Create budget:** set $1/month with email alerts. If anything
   ever starts costing money, you'll hear about it the same day.

## 2. Create the VM

**Compute Engine → VM instances → Create instance.** These settings keep it in the free tier:

| Setting | Value |
|---|---|
| Region | `us-central1`, `us-west1`, or `us-east1` (the only free-tier regions) |
| Machine type | `e2-micro` |
| Boot disk | Debian 12, **Standard persistent disk**, 30 GB ("Balanced" is *not* free) |
| Access scopes | "Set access for each API" → **Storage: Read Write** (only needed for offsite backups) |
| Firewall | ✅ Allow HTTP traffic, ✅ Allow HTTPS traffic |

Then **VPC network → IP addresses**, find the VM's external IP, and click **Reserve**. This makes it
static, so it doesn't change on reboot.

> **Check the bill after a day or two** (Billing → Reports). Google's pricing for external IPv4
> addresses has changed over time. If you see a charge, it's roughly a few dollars a month.

## 3. Point a hostname at the VM

HTTPS certificates need a hostname, not a bare IP. Pick one:

- **Your own domain** (~$10–15/yr): add an `A` record, e.g. `roster` → the VM's IP.
- **Free:** create a subdomain at https://www.duckdns.org and set it to the VM's IP.

Wait until `ping your-hostname` shows the VM's IP before continuing.

## 4. Install the app

Click **SSH** next to the VM in the console to open a terminal in your browser, then run:

```bash
sudo apt-get install -y git
sudo git clone https://github.com/Joseph-Bostok/music_machine.git /opt/labeldb
sudo /opt/labeldb/deploy/setup.sh roster.yourlabel.com
```

- **Private repo?** The clone needs credentials. The simplest option is a read-only
  [deploy key](https://docs.github.com/en/authentication/connecting-to-github-with-ssh/managing-deploy-keys).
- **Code not merged to the default branch yet?** Add `-b <branch-name>` to the clone.

Within about a minute Caddy fetches a certificate, and `https://roster.yourlabel.com` shows the sign-in page.

## 5. Import the spreadsheet

In the SSH window, click the **⚙ / Upload file** button and upload the `.xlsx`. It lands in your
home directory. Then run:

```bash
sudo install -m 644 ~/"SB Master File.xlsx" /var/lib/labeldb/import.xlsx
sudo labeldb importer /var/lib/labeldb/import.xlsx
sudo rm /var/lib/labeldb/import.xlsx ~/"SB Master File.xlsx"
```

## 6. Create logins

```bash
sudo labeldb users add owner@yourlabel.com     # prompts for a password (10+ characters)
sudo labeldb users add intern@yourlabel.com
sudo labeldb users list
sudo labeldb users passwd intern@yourlabel.com  # reset a password; signs them out everywhere
sudo labeldb users remove intern@yourlabel.com  # when someone leaves
```

Send people the link and their password over separate channels (e.g. the link by email, the
password by text).

## 7. Backups

Nightly at 03:30 (server time), a snapshot is written to `/var/lib/labeldb/backups/`, and the
newest 14 are kept. Check that the timer is scheduled:

```bash
systemctl list-timers labeldb-backup
sudo labeldb backup /var/lib/labeldb/backups   # take one right now
```

Local snapshots don't survive the VM being deleted. **For offsite copies:**

1. **Cloud Storage → Buckets → Create**, in the same free-tier region, *Standard* class.
   Up to 5 GB is free; each snapshot is well under 1 MB.
2. On the bucket's **Permissions** tab, grant the VM's service account
   (`<number>-compute@developer.gserviceaccount.com`) the role **Storage Object Creator**.
3. Re-run setup with the bucket name:
   `sudo /opt/labeldb/deploy/setup.sh roster.yourlabel.com gs://your-bucket-name`

**Restoring a snapshot:**

```bash
sudo systemctl stop labeldb
sudo -u labeldb cp /var/lib/labeldb/backups/label-YYYYMMDD-HHMMSS.db /var/lib/labeldb/label.db
sudo rm -f /var/lib/labeldb/label.db-wal /var/lib/labeldb/label.db-shm
sudo systemctl start labeldb
```

## Updating the app

```bash
cd /opt/labeldb
sudo git pull
sudo .venv/bin/pip install -q -r requirements.txt
sudo systemctl restart labeldb
```

## Troubleshooting

| Symptom | Check |
|---|---|
| Site doesn't load | `sudo systemctl status caddy labeldb` |
| App errors | `sudo journalctl -u labeldb -n 100` |
| Certificate errors | `sudo journalctl -u caddy -n 100`. Usually DNS doesn't point at the VM yet, or ports 80/443 aren't allowed in the firewall. |
| "Too many failed attempts" | Wait 15 minutes, or `sudo systemctl restart labeldb` to clear it |
