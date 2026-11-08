# Expense Tracker

Local Streamlit app. Each user gets a separate SQLite file in `data/` (filename = first 12 hex chars of
SHA-256 of the lower-cased name; optional PIN stored as a salted PBKDF2-SHA256 hash).

One shared virtualenv lives at the repo root (`Dashboards/.venv`); `.gitignore` and `push.sh` are at the root too.
Live data stays in this project's `data/` (git-ignored).

```bash
# once, from Dashboards/
python -m venv .venv && .venv/bin/pip install -r requirements.txt

# then, from Dashboards/ExpenseTracker/  (Streamlit reads .streamlit/config.toml from here)
../.venv/bin/streamlit run app.py
../.venv/bin/python seed.py --user Demo --reset   # optional demo data
../.venv/bin/python -m pytest -q
```

## Keeping your data safe (it is never committed)
The root `.gitignore` excludes `*.db`, `data/`, `backups/`, `*.csv` for every dashboard.

1. **Automatic snapshots**: one per day on app start and one before every delete → `backups/<user>/`.
   Restore from the app's **Backup** page.
2. **Off-machine copy**: set `EXPENSE_BACKUP_DIR` to a synced folder (Syncthing, Nextcloud, Drive, external disk), e.g.
   `export EXPENSE_BACKUP_DIR=~/Sync/expense-backups`. Also `EXPENSE_DATA_DIR` moves the live DBs.
3. **Scheduled**: `crontab -e` → `0 21 * * * cd /path/Dashboards/ExpenseTracker && ../.venv/bin/python backup.py`
4. **On every push**: run `./push.sh` from `Dashboards/` — it runs every project's `backup.py`, refuses to push if a data file is tracked, then `git push`.
5. Manual: Backup page → *Download database*, or History → *Export CSV*.

Never put the `.db` in a public repo. If you want git history of your data, use a separate **private** repo
(ideally with the file encrypted, e.g. `age`/`gpg`).
