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
def build_positions(securities: pd.DataFrame, trades: pd.DataFrame, dividends: pd.DataFrame):
    """Weighted-average-cost positions.

    Returns (positions, realised, warnings):
      positions - one row per security (open or closed) with qty, avg price, invested, value, P/L, weight, dividends
      realised  - one row per sell: date, security_id, pnl (sell proceeds - fees - avg cost of the sold units)
      warnings  - e.g. selling more units than were held
    """
    cols = ["id", "name", "symbol", "category", "reason", "qty", "avg_price", "invested", "first_buy", "last_buy",
            "current_price", "value", "pnl", "pnl_pct", "weight_pct", "realised", "dividends", "last_dividend",
            "dividend_count", "yield_on_cost_pct"]
    realised_rows, warnings, out = [], [], []
    for _, s in securities.iterrows():
        t = trades[trades["security_id"] == s["id"]].copy()
        t["_d"] = pd.to_datetime(t["date"])
        t = t.sort_values(["_d", "id"])
        qty = cost = realised = 0.0
        first = last = None
        for _, tr in t.iterrows():
            q, p, f = float(tr["qty"]), float(tr["price"]), float(pd.to_numeric(tr["fees"], errors="coerce") or 0)
            if tr["side"] == "Buy":
                cost += q * p + f
                qty += q
                first = first or tr["date"]
                last = tr["date"]
            else:
                if q > qty + EPS:
                    warnings.append(f"{s['name']}: sold {q:g} on {tr['date']} but only {qty:g} held; capped.")
                    q = qty
                if qty <= EPS:
                    continue
                avg = cost / qty
                pnl = q * p - f - avg * q
                realised += pnl
                realised_rows.append({"date": tr["date"], "security_id": s["id"], "pnl": pnl})
                cost -= avg * q
                qty -= q
        if qty <= EPS:
            qty, cost = 0.0, 0.0
        d = dividends[dividends["security_id"] == s["id"]]
        d_amt = float(num(d["amount"]).sum())
        avg_price = cost / qty if qty else 0.0
        cp = pd.to_numeric(s["current_price"], errors="coerce")
        price = avg_price if pd.isna(cp) or cp <= 0 else float(cp)
        value = qty * price
        out.append({
            "id": s["id"], "name": s["name"], "symbol": s["symbol"], "category": s["category"],
            "reason": s["reason"], "qty": qty, "avg_price": avg_price, "invested": cost,
            "first_buy": first, "last_buy": last, "current_price": price, "value": value,
            "pnl": value - cost, "pnl_pct": (value - cost) / cost * 100 if cost else 0.0, "weight_pct": 0.0,
            "realised": realised, "dividends": d_amt,
            "last_dividend": d["date"].max() if len(d) else None, "dividend_count": len(d),
            "yield_on_cost_pct": d_amt / cost * 100 if cost else 0.0,
        })
    pos = pd.DataFrame(out, columns=cols)
    total = pos["value"].sum()
    if total:
        pos["weight_pct"] = pos["value"] / total * 100
    realised_df = pd.DataFrame(realised_rows, columns=["date", "security_id", "pnl"])
    return pos, realised_df, warnings


# ---------------------------------------------------------------- income
def income_frame(income: pd.DataFrame, dividends: pd.DataFrame, realised: pd.DataFrame, include_auto=True) -> pd.DataFrame:
    """Unified income ledger: manual rows plus (optionally) stock dividends and realised P/L from the Stocks tab."""
    parts = [pd.DataFrame({"date": income["date"], "source": income["source"], "amount": num(income["amount"]),
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
def metals_frame(metals: pd.DataFrame, prices: dict[str, float]) -> pd.DataFrame:
    m = metals.copy()
    for c in ("grams", "rate_per_g", "making_charges", "gst", "other_charges"):
        m[c] = num(m[c])
    m["metal_cost"] = m["grams"] * m["rate_per_g"]
    m["charges"] = m["making_charges"] + m["gst"] + m["other_charges"]
    m["invested"] = m["metal_cost"] + m["charges"]
    m["current_price"] = m["metal"].map(prices).fillna(0.0)
    m["value"] = m["grams"] * m["current_price"]
    m["pnl"] = m["value"] - m["invested"]
    m["pnl_pct"] = (m["pnl"] / m["invested"].where(m["invested"] > 0)).fillna(0.0) * 100
    m["charges_pct"] = (m["charges"] / m["metal_cost"].where(m["metal_cost"] > 0)).fillna(0.0) * 100
    return m


def real_estate_frame(re_: pd.DataFrame) -> pd.DataFrame:
    r = re_.copy()
    for c in ("cost", "area_sqft", "registration", "brokerage", "other_charges", "monthly_rent", "current_value"):
        r[c] = num(r[c])
    r["extra_charges"] = r["registration"] + r["brokerage"] + r["other_charges"]
    r["total_cost"] = r["cost"] + r["extra_charges"]
    r["cost_per_sqft"] = (r["cost"] / r["area_sqft"].where(r["area_sqft"] > 0)).fillna(0.0)
    r["value"] = r["current_value"].where(r["current_value"] > 0, r["cost"])  # unknown market value -> cost
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
def bucket_values(positions: pd.DataFrame, metals: pd.DataFrame, real_estate: pd.DataFrame) -> pd.DataFrame:
    """Reality per investment bucket: current value and amount invested."""
    rows = []
    for b in db.SECURITY_CATEGORIES:
        p = positions[positions["category"] == b]
        rows.append((b, p["value"].sum(), p["invested"].sum()))
    for m in db.METALS:
        x = metals[metals["metal"] == m]
        rows.append((m, x["value"].sum(), x["invested"].sum()))
    rows.append(("Real Estate", real_estate["value"].sum(), real_estate["total_cost"].sum()))
    return pd.DataFrame(rows, columns=["bucket", "value", "invested"])


def allocation(buckets: pd.DataFrame, targets: dict[str, float]) -> pd.DataFrame:
    """Ideal (user target) vs reality per bucket, in rupees and as a share of each side's total."""
    a = buckets.copy()
    a["target"] = a["bucket"].map(targets).fillna(0.0)
    tv, tt = a["value"].sum(), a["target"].sum()
    a["actual_pct"] = a["value"] / tv * 100 if tv else 0.0
    a["target_pct"] = a["target"] / tt * 100 if tt else 0.0
    a["gap"] = a["target"] - a["value"]  # +ve: room left to invest, -ve: over target
    return a


def net_worth(invest_value: float, savings_total: float, loans_outstanding: float) -> float:
    return invest_value + savings_total - loans_outstanding


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
                  f"- Interest rate: {r['interest_rate']:g}% · EMI: {inr(r['emi'])}",
                  f"- Start: {r['start_date'] or '–'} · End: {r['end_date'] or '–'}"]
        if r["link"]:
            lines.append(f"- Documents: {r['link']}")
        if r["details"]:
            lines += ["", str(r["details"])]
        lines.append("")
    return "\n".join(lines)
