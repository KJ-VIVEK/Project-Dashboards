"""Aggregation, anomaly detection and forecasting. Pure functions over DataFrames."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta

import pandas as pd

OUTFLOW = ("fixed", "variable")


def monthly_summary(entries: pd.DataFrame) -> pd.DataFrame:
    """One row per month: inflow, fixed, variable, outflow, net, savings_rate, fixed_ratio."""
    cols = ["inflow", "fixed", "variable", "outflow", "net", "savings_rate", "fixed_ratio"]
    if entries.empty:
        return pd.DataFrame(columns=cols)
    df = entries.assign(month=entries["date"].dt.to_period("M").dt.to_timestamp())
    p = df.pivot_table(index="month", columns="type", values="amount", aggfunc="sum", fill_value=0.0)
    for c in ("inflow", "fixed", "variable"):
        if c not in p:
            p[c] = 0.0
    p = p[["inflow", "fixed", "variable"]].sort_index()
    full = pd.date_range(p.index.min(), p.index.max(), freq="MS")
    p = p.reindex(full, fill_value=0.0)
    p["outflow"] = p["fixed"] + p["variable"]
    p["net"] = p["inflow"] - p["outflow"]
    p["savings_rate"] = (p["net"] / p["inflow"]).where(p["inflow"] > 0)
    p["fixed_ratio"] = (p["fixed"] / p["inflow"]).where(p["inflow"] > 0)
    p.index.name = "month"
    return p


def yearly_comparison(monthly: pd.DataFrame, column: str = "outflow") -> pd.DataFrame:
    """Rows = month number 1..12, columns = years, for a year-on-year overlay chart."""
    if monthly.empty:
        return pd.DataFrame()
    t = monthly[[column]].copy()
    t["year"], t["m"] = t.index.year, t.index.month
    return t.pivot(index="m", columns="year", values=column).reindex(range(1, 13))


def category_monthly(entries: pd.DataFrame, types=OUTFLOW) -> pd.DataFrame:
    """Rows = month, columns = category, outflow amounts."""
    df = entries[entries["type"].isin(types)]
    if df.empty:
        return pd.DataFrame()
    df = df.assign(month=df["date"].dt.to_period("M").dt.to_timestamp())
    return df.pivot_table(index="month", columns="category", values="amount",
                          aggfunc="sum", fill_value=0.0).sort_index()


@dataclass
class Alert:
    level: str  # "danger" | "warning" | "info"
    title: str
    detail: str


def detect_alerts(entries: pd.DataFrame, today: date | None = None, window: int = 6,
                  sd_k: float = 1.5, pct_jump: float = 0.25, min_history: int = 3) -> list[Alert]:
    """Compare the current month against the trailing `window` full months."""
    today = today or date.today()
    alerts: list[Alert] = []
    if entries.empty:
        return alerts
    cur = pd.Timestamp(today).to_period("M").to_timestamp()
    past = entries[entries["date"] < cur]
    now = entries[(entries["date"] >= cur) & (entries["date"] <= pd.Timestamp(today))]

    # Per-category spike vs rolling history (variable spend + fixed categories)
    cm = category_monthly(past)
    if not cm.empty:
        cm = cm.reindex(pd.date_range(cm.index.min(), cur - pd.offsets.MonthBegin(1), freq="MS"),
                        fill_value=0.0).tail(window)
        spent = now[now["type"].isin(OUTFLOW)].groupby("category")["amount"].sum()
        for cat, amt in spent.items():
            if cat not in cm or len(cm) < min_history:
                continue
            hist = cm[cat]
            mean, sd = hist.mean(), hist.std(ddof=0)
            if mean <= 0:
                continue
            if amt > mean + sd_k * sd and amt > mean * (1 + pct_jump):
                alerts.append(Alert(
                    "danger" if amt > mean * 1.5 else "warning",
                    f"High {cat} spend",
                    f"₹{amt:,.0f} so far this month vs {len(hist)}-month avg ₹{mean:,.0f} "
                    f"(+{(amt / mean - 1) * 100:.0f}%)."))

    ms = monthly_summary(entries)
    if not ms.empty:
        full_cur = ms[ms.index == cur]
        if not full_cur.empty:
            r = full_cur.iloc[0]
            # skip the early-month false alarm before the first inflow is logged
            if r["outflow"] > r["inflow"] and r["outflow"] > 0 and (r["inflow"] > 0 or today.day >= 15):
                alerts.append(Alert("danger", "Negative cash flow",
                                    f"Outflow ₹{r['outflow']:,.0f} exceeds inflow ₹{r['inflow']:,.0f} this month."))
            if r["inflow"] > 0 and r["fixed_ratio"] > 0.5:
                alerts.append(Alert("warning", "Heavy fixed commitments",
                                    f"Fixed costs are {r['fixed_ratio'] * 100:.0f}% of inflow (above 50%)."))
        hist = ms[ms.index < cur].tail(window)
        if len(hist) >= min_history and not full_cur.empty:
            share = lambda d: d["variable"].sum() / d["outflow"].sum() if d["outflow"].sum() else 0
            s_now, s_hist = share(full_cur), share(hist)
            if s_hist > 0 and s_now > s_hist * (1 + pct_jump) and s_now > 0.2:
                alerts.append(Alert("warning", "Variable share rising",
                                    f"Variable spend is {s_now * 100:.0f}% of outflow vs {s_hist * 100:.0f}% typical."))
        # three consecutive months of rising outflow
        last = ms[ms.index < cur].tail(3)
        if len(last) == 3 and last["outflow"].is_monotonic_increasing and last["outflow"].nunique() == 3:
            alerts.append(Alert("info", "Outflow rising 3 months in a row",
                                " → ".join(f"₹{v:,.0f}" for v in last["outflow"])))

    order = {"danger": 0, "warning": 1, "info": 2}
    return sorted(alerts, key=lambda a: order[a.level])


def upcoming_fixed(fixed_items: pd.DataFrame, today: date | None = None, days: int = 30) -> pd.DataFrame:
    """Occurrences of active fixed items due within `days` (a short-cycle item may repeat)."""
    from db import _advance  # local import keeps analytics usable without db at import time

    today = today or date.today()
    end = today + timedelta(days=days)
    rows = []
    for _, it in fixed_items[fixed_items["active"] == 1].iterrows():
        due = date.fromisoformat(it["next_due"])
        while due <= end:
            if due >= today:
                rows.append({"name": it["name"], "category": it["category"], "amount": it["amount"],
                             "due": due, "in_days": (due - today).days})
            due = _advance(due, it["frequency"])
    out = pd.DataFrame(rows, columns=["name", "category", "amount", "due", "in_days"])
    return out.sort_values("due").reset_index(drop=True)


def budget_status(entries: pd.DataFrame, budgets: pd.DataFrame, today: date | None = None) -> pd.DataFrame:
    today = today or date.today()
    cur = pd.Timestamp(today).to_period("M").to_timestamp()
    spent = (entries[(entries["date"] >= cur) & entries["type"].isin(OUTFLOW)]
             .groupby("category")["amount"].sum())
    b = budgets.copy()
    b["spent"] = b["category"].map(spent).fillna(0.0)
    b["pct"] = (b["spent"] / b["amount"]).where(b["amount"] > 0, 0.0)
    return b


def forecast(entries: pd.DataFrame, fixed_items: pd.DataFrame, months: int = 3,
             today: date | None = None, window: int = 3) -> pd.DataFrame:
    """Project inflow / outflow / net for the next months.
    Inflow & variable = trailing-average; fixed = scheduled items (falls back to average)."""
    today = today or date.today()
    ms = monthly_summary(entries)
    cur = pd.Timestamp(today).to_period("M").to_timestamp()
    hist = ms[ms.index < cur].tail(window)
    if hist.empty:
        return pd.DataFrame(columns=["inflow", "fixed", "variable", "outflow", "net"])
    avg_in, avg_var, avg_fix = hist["inflow"].mean(), hist["variable"].mean(), hist["fixed"].mean()
    rows = {}
    active = fixed_items[fixed_items["active"] == 1] if not fixed_items.empty else fixed_items
    sched = upcoming_fixed(active, cur.date(), days=31 * months + 31) if len(active) else None
    for i in range(1, months + 1):
        m = cur + pd.DateOffset(months=i)
        fixed = avg_fix
        if sched is not None and len(sched):
            d = pd.to_datetime(sched["due"])
            fixed = sched.loc[(d.dt.year == m.year) & (d.dt.month == m.month), "amount"].sum() or avg_fix
        rows[m] = {"inflow": avg_in, "fixed": fixed, "variable": avg_var,
                   "outflow": fixed + avg_var, "net": avg_in - fixed - avg_var}
    return pd.DataFrame.from_dict(rows, orient="index")


def emergency_fund_months(balance: float, entries: pd.DataFrame, window: int = 6) -> float | None:
    ms = monthly_summary(entries)
    avg = ms["outflow"].tail(window).mean() if not ms.empty else 0
    return balance / avg if avg else None
