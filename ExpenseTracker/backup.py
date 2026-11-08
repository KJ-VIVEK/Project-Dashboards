"""Snapshot every user's database.   python backup.py [--keep 30]

Use it from cron / a systemd timer, or let push.sh call it before each `git push`.
Point EXPENSE_BACKUP_DIR at a folder that is synced elsewhere (Syncthing, Nextcloud,
Google Drive, an external disk) so a lost laptop does not mean lost data.
"""
import sys

import db


def main():
    keep = int(sys.argv[sys.argv.index("--keep") + 1]) if "--keep" in sys.argv else 30
    users = db.list_users()
    if not users:
        print("No users found in", db.DATA_DIR)
    for u in users:
        print(f"{u['name']:<20} -> {db.backup(u['path'], keep)}")


if __name__ == "__main__":
    main()
