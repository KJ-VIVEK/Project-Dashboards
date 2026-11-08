"""Snapshot the Asset Tracker database.   python backup.py [--keep 30]

Run from cron / a systemd timer, or let ../push.sh call it before each `git push`.
Point ASSET_BACKUP_DIR at a synced folder (Syncthing, Nextcloud, Drive, external disk).
"""
import os
import sys

import db


def main():
    keep = int(sys.argv[sys.argv.index("--keep") + 1]) if "--keep" in sys.argv else 30
    if not os.path.exists(db.DB_PATH):
        print("No database yet at", db.DB_PATH)
        return
    print(db.backup(db.DB_PATH, keep))


if __name__ == "__main__":
    main()
