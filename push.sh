#!/usr/bin/env bash
# Back up every dashboard's data, refuse to push data files, then push.  Usage: ./push.sh [git push args]
set -euo pipefail
cd "$(dirname "$0")"
for d in */; do
  [ -f "$d/backup.py" ] && (echo "== backup: $d" && cd "$d" && ../.venv/bin/python backup.py)
done
if git ls-files | grep -E '\.(db|db-wal|db-shm|sqlite3?|csv)$|(^|/)(data|backups)/'; then
  echo "ERROR: data files are tracked by git (listed above). Run: git rm --cached <file>" >&2; exit 1
fi
git push "$@"
