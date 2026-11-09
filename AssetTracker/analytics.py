"""Pure calculations for the Asset Tracker (no Streamlit, no I/O) so they can be unit-tested."""
from __future__ import annotations

import pandas as pd

import db

EPS = 1e-9


def num(s) -> pd.Series:
    return pd.to_numeric(s, errors="coerce").fillna(0.0)


def inr(v) -> str:
    """Indian digit grouping: 1234567 -> ₹12,34,567."""
    if v is None or pd.isna(v):
        return "–"
    n = int(round(abs(v)))
    s = str(n)
    if len(s) > 3:
        head, tail = s[:-3], s[-3:]
        parts = []
        while len(head) > 2:
            parts.insert(0, head[-2:])
            head = head[:-2]
        if head:
            parts.insert(0, head)
        s = ",".join(parts + [tail])
    return f"{'-' if v < 0 and n else ''}₹{s}"


# ---------------------------------------------------------------- stocks & funds
def _f(x) -> float:
    """Blank / NaN -> 0.0."""
    v = pd.to_numeric(x, errors="coerce")
    return 0.0 if pd.isna(v) else float(v)


def _ccy(x) -> str:
    """Only an explicit USD is foreign; blank/NaN/anything else is INR."""
    return "USD" if isinstance(x, str) and x.strip().upper() == "USD" else "INR"


def _fx(currency, trade_rate, now_rate) -> float:
    """INR per 1 unit of the holding's currency, for one trade (its own rate, else today's)."""
    if _ccy(currency) == "INR":
        return 1.0
    r = pd.to_numeric(trade_rate, errors="coerce")
    return float(r) if pd.notna(r) and r > 0 else (now_rate or 1.0)


def build_positions(securities: pd.DataFrame, trades: pd.DataFrame, dividends: pd.DataFrame, usd_inr: float = 0.0):
    """Weighted-average-cost positions, always reported in INR.

    A USD holding is bought at the FX rate stored on each trade (today's rate if missing) and valued at today's
    rate, so its P/L includes the currency move. Prices (avg buy, current) are shown in the holding's own currency.

    Returns (positions, realised, warnings):
      positions - one row per security (open or closed)
      realised  - one row per sell: date, security_id, pnl, cost_sold (INR)
      warnings  - e.g. selling more units than were held
    """
    cols = ["id", "name", "symbol", "category", "currency", "reason", "qty", "avg_price", "invested", "first_buy",
            "last_buy", "current_price", "price_date", "value", "pnl", "pnl_pct", "weight_pct", "realised",
            "dividends", "last_dividend", "dividend_count", "yield_on_cost_pct"]
    realised_rows, warnings, out = [], [], []
    for _, s in securities.iterrows():
        ccy = _ccy(s.get("currency"))
        t = trades[trades["security_id"] == s["id"]].copy()
        t["_d"] = pd.to_datetime(t["date"])
        t = t.sort_values(["_d", "id"])
        qty = cost = native = realised = 0.0   # cost in INR, native in the holding's currency
        first = last = None
        for _, tr in t.iterrows():
            q, p, f = float(tr["qty"]), float(tr["price"]), _f(tr["fees"])
            fx = _fx(ccy, tr.get("fx_rate"), usd_inr)
            if tr["side"] == "Buy":
                cost += (q * p + f) * fx
                native += q * p + f
                qty += q
                first = first or tr["date"]
                last = tr["date"]
            else:
                if q > qty + EPS:
                    warnings.append(f"{s['name']}: sold {q:g} on {tr['date']} but only {qty:g} held; capped.")
                    q = qty
                if qty <= EPS:
                    continue
                avg, avg_n = cost / qty, native / qty
                sold_cost = avg * q
                pnl = (q * p - f) * fx - sold_cost
                realised += pnl
                realised_rows.append({"date": tr["date"], "security_id": s["id"], "pnl": pnl, "cost_sold": sold_cost})
                cost -= sold_cost
                native -= avg_n * q
                qty -= q
        if qty <= EPS:
            qty, cost, native = 0.0, 0.0, 0.0
        d = dividends[dividends["security_id"] == s["id"]]
        d_amt = float(num(d["amount"]).sum())
        avg_native = native / qty if qty else 0.0
        cp = pd.to_numeric(s["current_price"], errors="coerce")
        price = avg_native if pd.isna(cp) or cp <= 0 else float(cp)
        value = qty * price * _fx(ccy, None, usd_inr)
        out.append({
            "id": s["id"], "name": s["name"], "symbol": s["symbol"], "category": s["category"], "currency": ccy,
            "reason": s["reason"], "qty": qty, "avg_price": avg_native, "invested": cost,
            "first_buy": first, "last_buy": last, "current_price": price, "price_date": s.get("price_date"),
            "value": value, "pnl": value - cost, "pnl_pct": (value - cost) / cost * 100 if cost else 0.0,
            "weight_pct": 0.0, "realised": realised, "dividends": d_amt,
            "last_dividend": d["date"].max() if len(d) else None, "dividend_count": len(d),
            "yield_on_cost_pct": d_amt / cost * 100 if cost else 0.0,
        })
    pos = pd.DataFrame(out, columns=cols)
    total = pos["value"].sum()
    if total:
        pos["weight_pct"] = pos["value"] / total * 100
    realised_df = pd.DataFrame(realised_rows, columns=["date", "security_id", "pnl", "cost_sold"])
    return pos, realised_df, warnings


# ---------------------------------------------------------------- income
def income_frame(income: pd.DataFrame, dividends: pd.DataFrame, realised: pd.DataFrame, include_auto=True) -> pd.DataFrame:
    """Unified income ledger: manual rows plus (optionally) stock dividends and realised P/L from the Stocks tab."""
    amount = num(income["amount"])
    amount = amount.where(income["source"] != db.TAX_SOURCE, -amount.abs())  # taxes reduce the total
    parts = [pd.DataFrame({"date": income["date"], "source": income["source"], "amount": amount,
                           "origin": "Source sheet"})]
    if include_auto:
        parts.append(pd.DataFrame({"date": dividends["date"], "source": "Dividend/FD",
                                   "amount": num(dividends["amount"]), "origin": "Stocks (dividend)"}))
        parts.append(pd.DataFrame({"date": realised["date"], "source": "PL Booking", "amount": realised["pnl"],
                                   "origin": "Stocks (sale)"}))
    df = pd.concat([p for p in parts if len(p)], ignore_index=True) if any(len(p) for p in parts) else \
        pd.DataFrame(columns=["date", "source", "amount", "origin"])
    dt = pd.to_datetime(df["date"], errors="coerce")
    df["year"] = dt.dt.year
    df["month"] = dt.dt.month
    return df.dropna(subset=["year"]).astype({"year": int, "month": int})


def income_pivot(df: pd.DataFrame, year: int | None, by="month") -> pd.DataFrame:
    """Rows = income source, columns = month 1..12 (by='month', for `year`) or years (by='year')."""
    sources = db.INCOME_SOURCES + sorted(set(df["source"]) - set(db.INCOME_SOURCES))
    if by == "month":
        d = df[df["year"] == year]
        p = d.pivot_table(index="source", columns="month", values="amount", aggfunc="sum", fill_value=0.0)
        p = p.reindex(index=sources, columns=range(1, 13), fill_value=0.0)
    else:
        p = df.pivot_table(index="source", columns="year", values="amount", aggfunc="sum", fill_value=0.0)
        p = p.reindex(index=sources, fill_value=0.0)
    return p.fillna(0.0)


# ---------------------------------------------------------------- gold / silver / property
def purity_factor(metal: str, purity) -> float:
    """Share of the reference price (24K gold / 999 silver) that a purity is worth.

    '22K' -> 22/24; '916', '925', '99.9%' -> fineness / 999. Blank or unreadable -> 1.0 (treated as pure).
    """
    p = str(purity or "").strip().upper().replace(" ", "")
    if not p or p in ("NAN", "NONE"):
        return 1.0
    try:
        if p.endswith("K"):
            return min(float(p[:-1]) / 24, 1.0) if metal == "Gold" else 1.0
        if p.endswith("%"):
            frac = float(p[:-1]) / 100
        else:
            v = float(p)
            frac = v / 1000 if v > 1 else v
        return min(frac / 0.999, 1.0) if 0 < frac <= 1 else 1.0
    except ValueError:
        return 1.0


def metals_frame(metals: pd.DataFrame, prices: dict[str, float]) -> pd.DataFrame:
    """Per-purchase numbers. `prices` are per gram for pure metal (24K gold / 999 silver); a holding's current price
    is that scaled by its purity (22K gold is worth 22/24 of the 24K price)."""
    m = metals.copy()
    for c in ("grams", "rate_per_g", "making_charges", "gst", "other_charges"):
        m[c] = num(m[c])
    m["metal_cost"] = m["grams"] * m["rate_per_g"]
    m["charges"] = m["making_charges"] + m["gst"] + m["other_charges"]
    m["invested"] = m["metal_cost"] + m["charges"]
    m["purity_factor"] = [purity_factor(a, b) for a, b in zip(m["metal"], m.get("purity", [None] * len(m)))]
    m["current_price"] = m["metal"].map(prices).fillna(0.0) * m["purity_factor"]
    m["value"] = m["grams"] * m["current_price"]
    m["pnl"] = m["value"] - m["invested"]
    m["pnl_pct"] = (m["pnl"] / m["invested"].where(m["invested"] > 0)).fillna(0.0) * 100
    m["charges_pct"] = (m["charges"] / m["metal_cost"].where(m["metal_cost"] > 0)).fillna(0.0) * 100
    return m


def real_estate_frame(re_: pd.DataFrame) -> pd.DataFrame:
    r = re_.copy()
    for c in ("cost", "area_sqft", "registration", "brokerage", "other_charges", "monthly_rent", "current_per_sqft"):
        r[c] = num(r[c])
    r["extra_charges"] = r["registration"] + r["brokerage"] + r["other_charges"]
    r["total_cost"] = r["cost"] + r["extra_charges"]
    r["cost_per_sqft"] = (r["cost"] / r["area_sqft"].where(r["area_sqft"] > 0)).fillna(0.0)
    # current value = today's price per sq ft x area; unknown price or area -> fall back to cost
    est = r["current_per_sqft"] * r["area_sqft"]
    r["value"] = est.where(est > 0, r["cost"])
    r["gain"] = r["value"] - r["total_cost"]
    r["annual_rent"] = r["monthly_rent"].where(r["rent_yielding"].astype(bool), 0.0) * 12
    r["rent_yield_pct"] = (r["annual_rent"] / r["total_cost"].where(r["total_cost"] > 0)).fillna(0.0) * 100
    return r


# ---------------------------------------------------------------- insurance / loans / savings
def insurance_frame(ins: pd.DataFrame) -> pd.DataFrame:
    i = ins.copy()
    i["premium"] = num(i["premium"])
    i["per_year"] = i["premium"] * i["frequency"].map(db.FREQUENCIES).fillna(1)
    i["per_month"] = i["per_year"] / 12
    i.loc[~i["active"].astype(bool), ["per_year", "per_month"]] = 0.0  # lapsed policies cost nothing going forward
    return i


def loans_frame(loans: pd.DataFrame) -> pd.DataFrame:
    l = loans.copy()
    for c in ("principal", "outstanding", "interest_rate", "emi"):
        l[c] = num(l[c])
    l["repaid_pct"] = ((l["principal"] - l["outstanding"]) / l["principal"].where(l["principal"] > 0)).fillna(0.0) * 100
    l["repaid_pct"] = l["repaid_pct"].clip(0, 100)
    if "status" not in l:
        l["status"] = "Active"
    l["status"] = l["status"].where(l["status"].isin(db.LOAN_STATUSES), "Active")
    closed = l["status"] == "Closed"
    l.loc[closed, "outstanding"] = 0.0          # a closed loan owes nothing, whatever was typed
    l.loc[closed, "repaid_pct"] = 100.0
    l.loc[closed, "emi"] = 0.0                  # and costs no EMI going forward
    end = l["closed_date"].where(closed, l["end_date"]) if "closed_date" in l else l["end_date"]

    def months(a, b):
        try:
            if a is None or b is None or pd.isna(a) or pd.isna(b):
                return None
            return max(0, (b.year - a.year) * 12 + (b.month - a.month))
        except AttributeError:
            return None
    l["tenure_months"] = [months(a, b) for a, b in zip(l["start_date"], end)]
    return l


def savings_totals(sv: pd.DataFrame) -> pd.DataFrame:
    s = sv.copy()
    s["amount"] = num(s["amount"])
    return s.groupby("kind", as_index=False)["amount"].sum().sort_values("amount", ascending=False)


def goal_progress(sv: pd.DataFrame) -> pd.DataFrame:
    g = sv[(sv["kind"] == "Short-term Goal")].copy()
    g["amount"], g["target"] = num(g["amount"]), num(g["target"])
    g["pct"] = (g["amount"] / g["target"].where(g["target"] > 0)).fillna(0.0).mul(100).clip(0, 100)
    return g


# ---------------------------------------------------------------- summary
def bucket_values(positions: pd.DataFrame, metals: pd.DataFrame, real_estate: pd.DataFrame,
                  savings_total: float = 0.0) -> pd.DataFrame:
    """Reality per bucket: current value and amount invested (cash & savings are valued at face value)."""
    rows = []
    for b in db.SECURITY_CATEGORIES:
        p = positions[positions["category"] == b]
        rows.append((b, p["value"].sum(), p["invested"].sum()))
    for m in db.METALS:
        x = metals[metals["metal"] == m]
        rows.append((m, x["value"].sum(), x["invested"].sum()))
    rows.append(("Real Estate", real_estate["value"].sum(), real_estate["total_cost"].sum()))
    rows.append((db.SAVINGS_BUCKET, savings_total, savings_total))
    return pd.DataFrame(rows, columns=["bucket", "value", "invested"])


def allocation(buckets: pd.DataFrame, pcts: dict[str, float], income: float) -> pd.DataFrame:
    """Ideal vs reality per bucket. Ideal = income x the % the user chose for that bucket.

    actual_pct is reality as a % of that same income, so it is directly comparable with the ideal %.
    gap = ideal - reality (positive: room left to invest, negative: over target).
    """
    a = buckets.copy()
    a["target_pct"] = a["bucket"].map(pcts).fillna(0.0)
    a["target"] = income * a["target_pct"] / 100
    a["actual_pct"] = a["value"] / income * 100 if income else 0.0
    a["gap"] = a["target"] - a["value"]
    return a


def net_worth(invest_value: float, savings_total: float, loans_outstanding: float) -> float:
    return invest_value + savings_total - loans_outstanding


def money_flow(income_total: float, trades_flow: dict, metals: pd.DataFrame, real_estate: pd.DataFrame,
               savings: pd.DataFrame, year: int | None = None) -> dict:
    """Where the income went in a period (a year, or all time when year is None).

        Expense = Income - Investments - Savings

    Investments = new money put into assets in the period: cost of stock/fund buys minus the cost basis of units
    sold (so recycled capital is not counted twice; profit from sells is already in income), gold/silver bought
    (incl. charges) and property bought (incl. charges), by purchase date. Savings = cash/FD/EPF/NPS/goal balances;
    for a single year only those with a start date in that year can be attributed to it.
    """
    def in_year(series):
        d = pd.to_datetime(series, errors="coerce")
        return d.dt.year == year if year is not None else pd.Series(True, index=series.index)

    m = metals[in_year(metals["date"])]["invested"].sum() if len(metals) else 0.0
    r = real_estate[in_year(real_estate["purchase_date"])]["total_cost"].sum() if len(real_estate) else 0.0
    sv = savings
    if year is not None and len(sv):
        sv = sv[in_year(sv["start_date"])]
    saved = float(num(sv["amount"]).sum()) if len(sv) else 0.0
    stocks = float(trades_flow.get(year if year is not None else "all", 0.0))
    invested = stocks + m + r
    return {"income": income_total, "stocks": stocks, "metals": float(m), "real_estate": float(r),
            "invested": invested, "saved": saved, "expense": income_total - invested - saved,
            "savings_rate_pct": (invested + saved) / income_total * 100 if income_total else 0.0}


def stock_net_invested(trades: pd.DataFrame, securities: pd.DataFrame, realised: pd.DataFrame, usd_inr: float) -> dict:
    """Net new money put into stocks/funds (INR) per year and 'all': buys minus cost basis of units sold."""
    ccy = dict(zip(securities["id"], securities["currency"]))
    flow: dict = {}
    for _, t in trades[trades["side"] == "Buy"].iterrows():
        fx = _fx(ccy.get(t["security_id"]), t.get("fx_rate"), usd_inr)
        amt = (float(t["qty"]) * float(t["price"]) + _f(t["fees"])) * fx
        y = pd.to_datetime(t["date"]).year
        flow[y] = flow.get(y, 0.0) + amt
    for _, r in realised.iterrows():
        y = pd.to_datetime(r["date"]).year
        flow[y] = flow.get(y, 0.0) - float(r["cost_sold"])
    flow["all"] = sum(v for k, v in flow.items() if k != "all")
    return flow


def loans_markdown(loans: pd.DataFrame, title="Loan summary") -> str:
    """A read-only, shareable write-up of the loans (hand it to a partner, advisor or family member)."""
    l = loans_frame(loans)
    lines = [f"# {title}", "", f"Total outstanding: {inr(l['outstanding'].sum())} · "
             f"Total EMI per month: {inr(l['emi'].sum())}", ""]
    for _, r in l.iterrows():
        lines += [f"## {r['name']}  ({r['category']} debt)", "",
                  f"- Lender: {r['lender'] or '–'}",
                  f"- Principal: {inr(r['principal'])} · Outstanding: {inr(r['outstanding'])} "
                  f"({r['repaid_pct']:.0f}% repaid)",
                  f"- Status: {r['status']}"
                  + (f" (closed {r['closed_date']})" if r['status'] == 'Closed' and r['closed_date'] else "")
                  + (f" · Tenure: {int(r['tenure_months'])} months" if pd.notna(r['tenure_months']) else ""),
                  f"- Interest rate: {r['interest_rate']:g}% · EMI: {inr(r['emi'])}",
                  f"- Start: {r['start_date'] or '–'} · End: {r['end_date'] or '–'}"]
        if r["link"]:
            lines.append(f"- Documents: {r['link']}")
        if r["details"]:
            lines += ["", str(r["details"])]
        lines.append("")
    return "\n".join(lines)
