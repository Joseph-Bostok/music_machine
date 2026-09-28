"""Snapshot the database, keep the last N copies, optionally upload offsite.

    python -m labeldb.backup /var/lib/labeldb/backups [--keep 14] [--bucket gs://my-bucket]

The bucket can also come from $LABEL_BACKUP_BUCKET, which is how the
nightly systemd timer passes it.

Why not just `cp label.db`? Copying a SQLite file while the app is writing
can capture half a transaction. The sqlite3 backup API copies a consistent
snapshot even while the app is running.
"""

import argparse
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from labeldb import db


def snapshot(source_conn, dest_dir):
    dest_dir = Path(dest_dir)
    dest_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S")
    path = dest_dir / f"label-{stamp}.db"
    dest = db.sqlite3.connect(path)
    with dest:
        source_conn.backup(dest)
    dest.close()
    return path


def prune(dest_dir, keep):
    """Delete all but the newest `keep` snapshots. Timestamped names sort
    chronologically, so sorting by name is sorting by age."""
    snapshots = sorted(Path(dest_dir).glob("label-*.db"))
    for old in snapshots[:-keep] if keep > 0 else []:
        old.unlink()
    return snapshots[-keep:] if keep > 0 else snapshots


def main(argv=None):
    parser = argparse.ArgumentParser(description="Back up the label database.")
    parser.add_argument("dest_dir", type=Path)
    parser.add_argument("--keep", type=int, default=14, help="local snapshots to keep")
    parser.add_argument("--bucket", default=os.environ.get("LABEL_BACKUP_BUCKET"),
                        help="also copy to this Cloud Storage bucket, gs://... "
                             "(default: $LABEL_BACKUP_BUCKET)")
    args = parser.parse_args(argv)

    path = snapshot(db.connect(), args.dest_dir)
    prune(args.dest_dir, args.keep)
    print(f"Wrote {path}")

    if args.bucket:
        # The VM's service account authenticates gcloud; no keys on disk.
        result = subprocess.run(["gcloud", "storage", "cp", str(path), args.bucket.rstrip("/") + "/"])
        if result.returncode != 0:
            sys.exit("Upload to Cloud Storage failed; the local snapshot was still written.")


if __name__ == "__main__":
    main()
