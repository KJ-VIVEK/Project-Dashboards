# Asset Tracker

Local Streamlit app for your whole balance sheet, backed by one SQLite file (`data/assets.db`, git-ignored).
Share the repo-root virtualenv with the other dashboards (see `../ExpenseTracker/README.md`).

```bash
# from Dashboards/AssetTracker/
../.venv/bin/streamlit run app.py
../.venv/bin/python seed.py                       # fake demo data -> data/demo.db (never touches assets.db)
ASSET_DB=data/demo.db ../.venv/bin/streamlit run app.py
../.venv/bin/python -m pytest -q
```

## Tabs
| Tab | What it does |
|---|---|
| **Summary** | Net worth, income by month/source, and **ideal vs reality** per investment bucket (ideal ₹ values are editable). Updates as soon as any other tab changes. |
| **Source** | Income entries (Salary, Dividend/FD, PL Booking, Miscellaneous); per-month and per-year views; edit or delete any row. |
| **Cash & Savings** | Savings account, FDs (with purpose and maturity), EPF, NPS, PPF/RD, short-term goals with progress bars. |
| **Insurance** | Policies with links, sum assured, premium per month / per year, upcoming dues, active/lapsed. |
| **Loans** | Good / Bad / Worst debt, EMI, links, free-text details, and a downloadable Markdown write-up to share. |
| **Stocks** | Holdings by category (India high-quality/risky, index funds, MFs, ETFs, international stable/risky, REITs/InvITs, unlisted, crypto). Record buys, sells and dividends; average cost, % of portfolio, P/L, dividends received and the reason for each holding are calculated. |
| **Gold & Silver** | Physical purchases with making charges, GST and other charges; valued at the per-gram price you type. |
| **Real Estate** | Cost, cost per sq ft, extra charges, rent yield, current value. |
| **Notes** | Todos / ideas: add, edit, mark done, delete. |

## How numbers are computed
- **Stocks** use weighted-average cost (buy fees included in cost). A sell realises `proceeds − fees − avg cost × units`
  and leaves the average price unchanged. Without a typed current price, a holding is valued at cost.
- **Summary income** = Source-sheet rows + stock dividends (as Dividend/FD) + realised sell P/L (as PL Booking).
  Untick the toggle on the Summary tab if you also type those on the Source sheet.
- **Net worth** = investments (current value) + cash & savings − loans outstanding.
- Amounts are in ₹; enter international holdings already converted.

## Your data
`*.db`, `data/`, `backups/` are git-ignored. A snapshot is taken daily on app start and before any delete
(`backups/`); set `ASSET_BACKUP_DIR` to a synced folder for an off-machine copy. `../push.sh` runs `backup.py`
before pushing. Use `ASSET_DB` to point at a different database file.
