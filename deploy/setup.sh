#!/usr/bin/env bash
# One-time server setup for a fresh Debian 12 VM. Safe to re-run.
#
#   sudo git clone <repo-url> /opt/labeldb
#   sudo /opt/labeldb/deploy/setup.sh roster.example.com [gs://backup-bucket]
#
# Installs Python + Caddy, creates an unprivileged `labeldb` user, and starts
# the app behind HTTPS with nightly backups. See DEPLOY.md for the full walkthrough.
set -euo pipefail

DOMAIN="${1:?usage: setup.sh DOMAIN [gs://BACKUP_BUCKET]}"
BUCKET="${2:-}"
APP_DIR=/opt/labeldb
DATA_DIR=/var/lib/labeldb

[[ $EUID -eq 0 ]] || { echo "Run with sudo." >&2; exit 1; }
[[ -f $APP_DIR/labeldb/app.py ]] || { echo "Clone the repo to $APP_DIR first." >&2; exit 1; }

echo "==> Installing packages"
apt-get update -q
apt-get install -y -q python3-venv caddy unattended-upgrades
# Security updates install automatically.
dpkg-reconfigure -f noninteractive unattended-upgrades

echo "==> Creating service user and data directory"
id labeldb &>/dev/null || useradd --system --home-dir "$DATA_DIR" --shell /usr/sbin/nologin labeldb
install -d -o labeldb -g labeldb -m 750 "$DATA_DIR" "$DATA_DIR/backups"

echo "==> Installing the app"
python3 -m venv "$APP_DIR/.venv"
"$APP_DIR/.venv/bin/pip" install -q --upgrade pip
"$APP_DIR/.venv/bin/pip" install -q -r "$APP_DIR/requirements.txt"

echo "==> Installing systemd units"
install -m 644 "$APP_DIR"/deploy/labeldb.service "$APP_DIR"/deploy/labeldb-backup.service \
    "$APP_DIR"/deploy/labeldb-backup.timer /etc/systemd/system/
install -m 755 "$APP_DIR/deploy/labeldb" /usr/local/bin/labeldb
install -d /etc/labeldb
if [[ -n $BUCKET ]]; then
    echo "LABEL_BACKUP_BUCKET=$BUCKET" > /etc/labeldb/backup.env
fi

echo "==> Configuring Caddy (automatic HTTPS for $DOMAIN)"
cat > /etc/caddy/Caddyfile <<EOF
$DOMAIN {
    encode gzip
    reverse_proxy 127.0.0.1:8000
    header {
        Strict-Transport-Security "max-age=31536000"
        X-Content-Type-Options nosniff
        X-Frame-Options DENY
        Referrer-Policy same-origin
        -Server
    }
}
EOF

systemctl daemon-reload
systemctl enable --now labeldb labeldb-backup.timer
systemctl restart labeldb
systemctl reload caddy

echo
echo "Done. Next:"
echo "  1. Import data:   see 'Import the spreadsheet' in DEPLOY.md"
echo "  2. Add a login:   sudo labeldb users add you@example.com"
echo "  3. Open:          https://$DOMAIN"
