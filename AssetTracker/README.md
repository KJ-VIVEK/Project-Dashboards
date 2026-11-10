# 💼 Asset Tracker

> Your whole balance sheet in one local app: income, savings, insurance, loans, stocks, gold, property and businesses,
> with a Summary that shows where your money came from, where it went, and how that compares with your plan.

Everything is stored in one SQLite file on your machine (`data/assets.db`). Nothing is sent anywhere except price
look-ups (see [Live prices](#-live-prices-and-usd)), and your data is never committed to git.

---

## 🚀 Quick start

Run these from the `AssetTracker/` folder. The virtual environment is shared with the other dashboards
(set it up once, see the [root README](../README.md)).

```bash
../.venv/bin/streamlit run app.py                 # start the app at http://localhost:8502

../.venv/bin/python seed.py                       # make a fake demo database (data/demo.db, never touches your data)
ASSET_DB=data/demo.db ../.venv/bin/streamlit run app.py     # ...and open the app on it

../.venv/bin/python -m pytest -q                  # run the tests
```

---

## 🗂️ What is in the app

| Tab | What you do there |
|---|---|
| 📊 **Summary** | Net worth, income by month and source, **where the income went**, and **plan vs reality**. Pick *All time* or a single year. |
| 💰 **Source** | Record income: salary, RSUs, dividends/FD, profit booked, miscellaneous. Taxes, extra tax and a year-on-year growth chart. |
| 🏦 **Cash & Savings** | Savings accounts, FDs, RDs, PPF, EPF, NPS, cash and short-term goals. |
| 🛡️ **Insurance** | Policies, premiums, who is covered, start/end dates. |
| 🏠 **Loans** | Good / Bad / Worst debt, with the real cost of each loan. |
| 📈 **Stocks** | Holdings by category, trades, dividends, profit/loss. Live prices. |
| 🥇 **Gold & Silver** | Physical purchases, valued at the live price. |
| 🧱 **Real Estate** | Properties, cost per sq ft, rent yield, current value. |
| 🏪 **Business** | Businesses and trades: invested, made, lost, running or closed. |
| 📝 **Notes** | Simple to-dos and ideas. |

The sidebar refreshes live prices, lets you override the USD→₹ rate, takes a backup, **restores from one** and downloads the database.

### 📊 Summary

- **Income**: your Source entries plus stock dividends and profit from sells. Switches let you leave out the
  automatic parts if you also type them on the Source tab.
- **Where the income went**: income → investments, savings, insurance, loan costs → what is left (expenses),
  shown as numbers and a flow chart. See [the formula](#-how-the-numbers-are-worked-out).
- **Plan vs reality**: split your income into categories (a % each), then compare with what actually happened.
  Open **Set ideal values** to edit the plan.

  | Category | Can be divided further? |
  |---|---|
  | Savings · Real-Estate · Business & Trading · EMI · Expenses & Leisure | No, one % of income each |
  | Investment | Yes, into types such as Mutual Funds, Index Funds & ETFs, Gold & Silver |

  Example: *Investment 40% of income* and *Mutual Funds 50% of Investment* gives Mutual Funds **20% of income**.
  Saving checks everything first (totals within 100%, no repeated or unnamed rows) and takes a backup.

### 💰 Source

- Each entry has its own **Taxes paid** (subtracted) and **EPF / NPS / Vouchers / Insurance** (recorded only, never subtracted).
- Amounts can be **negative**, for example a loss from closing an FD early.
- **Extra tax %** for Dividend/FD, PL Booking and Miscellaneous: set a default on the tab, or type one on a row.
  The extra tax is subtracted from the final value.
- **RSUs**: enter shares vested and the buy price, plus the shares sold to cover tax with their sell price and date.
  Kept shares are valued at today's USD→₹ rate. Sold shares are fixed at that day's rate and that value is the tax.
- A year-on-year chart shows income before tax (green), after tax (orange), the average growth (dotted) and tax paid.

### 🏦 Cash & Savings

- Deposits always have a start and an end date, and show interest earned and whether they renew.
- **Temporarily closed** deposits are taken out of every total until you add them back.
- Goals have a target amount and show progress.

### 🛡️ Insurance and 🏠 Loans

- Insurance premiums are counted on the Summary on the dates actually paid (a yearly premium in full).
- A vehicle is two ordinary policies: Own Damage and Third Party.
- Each loan has a card showing where the money went (down payment, instalments, interest, closing balance, charges),
  the interest saved by closing early, and a downloadable Markdown write-up.

### 📈 Stocks · 🥇 Gold & Silver · 🧱 Real Estate · 🏪 Business

- **Stocks** are listed category by category, biggest first, each with its share of the total invested.
  Average cost, profit/loss, dividends and the reason for each holding are worked out for you.
- **Gold & Silver** follow the spot price × USD/INR plus a local premium you can tune.
- **Real Estate** current value = price per sq ft today × area.
- **Business**: net result = money made − money lost; ROI = net result ÷ invested.
  *Overall gain/loss* also takes the capital you put in (and a value now, if you enter one) into account.

---

## 🔢 How the numbers are worked out

```
Expenses (left over) = Income − Invested − Saved − Insurance − Loan interest & charges
```

| Term | What it contains |
|---|---|
| **Income** | Source entries after tax, plus stock dividends and profit booked on sells. |
| **Invested** | New money into stocks/funds (buys minus cost of units sold), gold & silver, property (with charges), businesses, **and EPF and NPS** (they are invested in funds, though you enter them under Cash & Savings). |
| **Saved** | Cash, FD, RD, PPF and goal balances. |
| **Insurance** | Premiums actually paid in the period. |
| **Loan interest & charges** | Interest and other charges only. The down payment and principal are not counted: they bought an asset or are part of your spending. |

- For a single year, only entries *dated in that year* are counted. Use *All time* for entries without dates.
- Insurance and loan costs can each be switched off on the Summary.
- Loan dates are estimates: the Loans tab stores totals, so interest is spread over the instalments.
- **Net worth** = investments (current value) + cash & savings − loans outstanding.
- **Stocks** use weighted-average cost (buy fees included). A sell realises *proceeds − fees − average cost × units*.
- All totals are in ₹.

---

## 📡 Live prices and USD

- **Symbol** on a holding turns on live pricing:
  `RELIANCE.NS` / `TCS.BO` (Indian stocks and ETFs), `AAPL` (US), `BTC-INR` (crypto), or an AMFI scheme code like
  `119551` (mutual fund NAV). Leave it blank for anything you price yourself.
- **USD holdings**: set the currency to USD and enter prices in dollars. Each trade stores that day's USD→₹ rate, so the
  rupee cost is locked in, while the current value uses the live rate (profit/loss includes the currency move).
- The sidebar refreshes on load and every 1 / 5 / 15 minutes (or *Off*), plus **Refresh now**.
- Sources: Yahoo Finance (quotes, FX, spot), open.er-api.com (FX fallback), mfapi.in (NAV). No keys needed.
  If a source fails, the last stored price stays. Quotes can be delayed and the endpoints are unofficial: treat values as indicative.
- Set `ASSET_OFFLINE=1` to turn all network calls off.
- Real estate, unlisted shares and cash have no live source: update them by hand.

---

## ✅ Safe editing

- **Nothing is deleted silently.** If a save would remove rows, the app lists them and asks you to confirm
  (and takes a backup first).
- **Every edit is checked** before saving: required values, numbers above zero, valid choices, dates in order and between
  1995 and 2100. If something is wrong you get a list of rows and fields, nothing is saved, and what you typed stays put.
  The rules live in `validation.py`.

## 💾 Your data

`*.db`, `data/` and `backups/` are git-ignored.

| Backup | When |
|---|---|
| Daily snapshot | On app start |
| Before any delete, or saving the plan | Automatically |
| **Back up now** | Sidebar button |
| **Restore from a backup** | Sidebar: pick a snapshot and confirm. Your current data is backed up first, so nothing is lost. |
| Off-machine copy | Set `ASSET_BACKUP_DIR` to a synced folder |
| On push | `../push.sh` runs `backup.py` first |

### Settings

| Environment variable | Effect |
|---|---|
| `ASSET_DB` | Use a different database file (for example the demo) |
| `ASSET_BACKUP_DIR` | Where backups are written |
| `ASSET_OFFLINE=1` | No network calls |

## 🧩 Code map

| File | Role |
|---|---|
| `app.py` | The Streamlit interface (all tabs) |
| `analytics.py` | All the maths: income, flows, loans, plan, positions |
| `db.py` | SQLite schema, migrations, reads and writes, backups |
| `validation.py` | The rules every edit is checked against |
| `market.py` | Live prices and exchange rates |
| `seed.py` · `backup.py` | Demo data · snapshot script |
| `test_analytics.py` · `test_market.py` | Tests |
