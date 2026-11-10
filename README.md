# 📚 Personal Dashboards

Small, private, local dashboards for personal finance. Each one is a Streamlit app with its own SQLite database.
Your data stays on your machine and is never committed to git.

| Dashboard | What it does |
|---|---|
| 💼 [**Asset Tracker**](AssetTracker/README.md) | Net worth, income, savings, insurance, loans, stocks, gold, property and businesses, plus a plan-vs-reality Summary |
| 🧾 [**Expense Tracker**](ExpenseTracker/README.md) | Day-to-day expenses with budgets, fixed items and insights, one private database per user |

## 🚀 Setup (once)

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt
```

One virtual environment at this level serves every dashboard. Then open a dashboard's folder and follow its README:

```bash
cd AssetTracker        # or ExpenseTracker
../.venv/bin/streamlit run app.py
```

Each dashboard has its own port, so you can run both at once:

| Dashboard | Address |
|---|---|
| Asset Tracker | http://localhost:8502 |
| Expense Tracker | http://localhost:8503 |

The ports are set in each dashboard's `.streamlit/config.toml`.

## 🔐 Your data

- `*.db`, `*.csv`, `data/` and `backups/` are git-ignored for every dashboard.
- The only database in git is `ExpenseTracker/demo/demo.db`, which is fake sample data.
- Each app takes a daily snapshot and one before any delete.
- **`./push.sh`** backs up every dashboard, refuses to push if a data file is tracked, then runs `git push`.

## 🗺️ Layout

```
Dashboards/
├── AssetTracker/      app, maths, database, tests
├── ExpenseTracker/    app, maths, database, tests
├── requirements.txt   shared dependencies
└── push.sh            back up, check, then push
```
