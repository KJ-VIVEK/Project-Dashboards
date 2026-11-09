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
| **Summary** | Net worth; income by month/source; **where the income went** (income → investments, savings, expenses); and **ideal vs reality** per bucket. Period selector: All time or a single year. Updates as soon as any other tab changes. |
| **Source** | Income entries (Salary, Dividend/FD, PL Booking, Miscellaneous); per-month and per-year views; edit or delete any row. |
| **Cash & Savings** | Savings account, FDs (with purpose and maturity), EPF, NPS, PPF/RD, short-term goals with progress bars. |
| **Insurance** | Policies with links, sum assured, premium per month / per year, upcoming dues, active/lapsed. |
| **Loans** | Good / Bad / Worst debt, EMI, links, free-text details, and a downloadable Markdown write-up to share. |
| **Stocks** | Holdings by category (India high-quality/risky, index funds, MFs, ETFs, international stable/risky, REITs/InvITs, unlisted, crypto). Record buys, sells and dividends; average cost, % of portfolio, P/L, dividends received and the reason for each holding are calculated. |
| **Gold & Silver** | Physical purchases with making charges, GST and other charges; valued at the per-gram price you type. |
| **Real Estate** | Cost, cost per sq ft, extra charges, rent yield. Current value = today's price per sq ft × area. |
| **Notes** | Todos / ideas: add, edit, mark done, delete. |

## Live prices and USD
- **Symbol** on a holding turns on live pricing: `RELIANCE.NS` / `TCS.BO` (Indian stocks and ETFs), `AAPL` (US),
  `BTC-INR` (crypto), or a numeric AMFI scheme code like `119551` (mutual-fund NAV). Leave it blank for unlisted
  shares or anything you price yourself.
- **USD holdings**: set the holding's currency to USD and enter prices in dollars. Each trade stores the USD→INR rate
  of that day (defaults to today's), so the rupee cost is locked in; current value uses the live rate, so P/L
  includes the currency move. Dividends are entered in ₹ as credited to your bank.
- **Gold and silver** follow the international spot price × USD/INR, plus a local-premium % you tune to match your
  jeweller (duty and GST make Indian prices higher).
- The sidebar refreshes everything on load and every 1 / 5 / 15 min (default 5; "Off" disables it), plus **Refresh
  now**. You can override the USD→INR rate. Sources: Yahoo Finance (quotes, FX, spot), open.er-api.com (FX
  fallback), mfapi.in (NAV). No keys needed. If a source fails, the last stored price stays. `ASSET_OFFLINE=1`
  turns all network calls off.
- Quotes can be delayed a few minutes and the endpoints are unofficial: treat values as indicative.
- Real estate, unlisted shares and cash have no live source; update them by hand.

## How numbers are computed
- **Stocks** use weighted-average cost (buy fees included in cost). A sell realises `proceeds − fees − avg cost × units`
  and leaves the average price unchanged. Without a typed current price, a holding is valued at cost.
- **Summary income** = Source-sheet rows + stock dividends (as Dividend/FD) + realised sell P/L (as PL Booking).
  Untick the toggle on the Summary tab if you also type those on the Source sheet.
- **Ideal** per bucket = income (for the selected period) × the % you set. The unallocated % is your expense budget.
  "Actual % of income" puts reality on the same scale.
- **Expenses = Income − Investments − Savings.** Investments are new money put into stocks/funds (buys minus the cost
  of units sold), gold/silver and property (including charges), by purchase date. Savings are cash, FD, EPF, NPS and
  goal balances; for a single year only entries with a *start date* in that year are attributed to it, so use
  "All time" if you do not fill start dates.
- **Net worth** = investments (current value) + cash & savings − loans outstanding.
- All totals are in ₹.

## Your data
`*.db`, `data/`, `backups/` are git-ignored. A snapshot is taken daily on app start and before any delete
(`backups/`); set `ASSET_BACKUP_DIR` to a synced folder for an off-machine copy. `../push.sh` runs `backup.py`
before pushing. Use `ASSET_DB` to point at a different database file.
