# 🧾 Expense Tracker

> A private, local expense diary. Each person gets their own database, with optional PIN protection.

Built with Streamlit and SQLite. Your data stays in this project's `data/` folder and is never committed to git.

---

## 🚀 Quick start

The virtual environment is shared by all dashboards and lives at the repo root (see the [root README](../README.md)).

```bash
# once, from Dashboards/
python -m venv .venv && .venv/bin/pip install -r requirements.txt

# then, from Dashboards/ExpenseTracker/  (Streamlit reads .streamlit/config.toml from here)
../.venv/bin/streamlit run app.py                 # opens at http://localhost:8503

../.venv/bin/python seed.py --from-demo           # install the sample "Demo" user (demo/demo.db)
../.venv/bin/python seed.py --user Demo --reset   # or generate fresh random demo data
../.venv/bin/python -m pytest -q                  # run the tests
```

## 📄 Pages

| Page | What it is for |
|---|---|
| **Dashboard** | The overview of your spending |
| **Add entry** | One-off money in or out, and your recent entries |
| **Fixed items** | Recurring bills such as rent, subscriptions and EMIs, added for you automatically |
| **Budgets** | A monthly budget per category, with how much of it is spent |
| **Insights** | A 3-month forecast and patterns in your spending |
| **History** | Every entry, with CSV export |
| **Backup** | Download, restore and manage backups |

## 👤 Users and privacy

- Each user has a separate SQLite file in `data/`. The file name is the first 12 hex characters of the SHA-256 of the
  lower-cased name.
- An optional PIN is stored only as a salted PBKDF2-SHA256 hash.

## 💾 Keeping your data safe

The root `.gitignore` excludes `*.db`, `data/`, `backups/` and `*.csv` for every dashboard.
The only database in git is `demo/demo.db`: fake sample data, safe to share.

| # | Safeguard | How |
|---|---|---|
| 1 | **Automatic snapshots** | One per day on app start and one before every delete, in `backups/<user>/`. Restore from the **Backup** page. |
| 2 | **Off-machine copy** | Set `EXPENSE_BACKUP_DIR` to a synced folder (Syncthing, Nextcloud, Drive, an external disk), for example `export EXPENSE_BACKUP_DIR=~/Sync/expense-backups`. |
| 3 | **Scheduled** | `crontab -e`, then `0 21 * * * cd /path/Dashboards/ExpenseTracker && ../.venv/bin/python backup.py` |
| 4 | **On every push** | Run `./push.sh` from `Dashboards/`. It backs up every project, refuses to push if a data file is tracked, then runs `git push`. |
| 5 | **Manual** | Backup page → *Download database*, or History → *Export CSV*. |

`EXPENSE_DATA_DIR` moves the live databases elsewhere.

> ⚠️ Never put a `.db` file in a public repo. If you want git history of your data, use a separate **private** repo,
> ideally with the file encrypted (for example with `age` or `gpg`).

## 🧩 Code map

| File | Role |
|---|---|
| `app.py` | The Streamlit interface |
| `analytics.py` | The calculations |
| `db.py` | Users, SQLite access, backups |
| `captions.py` | Short explanatory texts |
| `seed.py` · `backup.py` | Demo data · snapshot script |
| `test_analytics.py` | Tests |
