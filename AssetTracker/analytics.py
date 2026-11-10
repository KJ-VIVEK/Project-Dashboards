"""Pure calculations for the Asset Tracker (no Streamlit, no I/O) so they can be unit-tested."""
from __future__ import annotations

from datetime import date

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
def apply_editor_edits(df: pd.DataFrame, edited_rows: dict | None) -> pd.DataFrame:
    """The table as it looks with the cells typed so far in a data_editor (its `edited_rows`: {row position: {column:
    value}}), so figures worked out from those cells can be shown before anything is saved."""
    out = df.reset_index(drop=True).copy()
    for pos, changes in (edited_rows or {}).items():
        pos = int(pos)
        if 0 <= pos < len(out):
            for col, val in changes.items():
                if col in out.columns:
                    out[col] = out[col].astype(object)
                    out.at[pos, col] = val
    return out


def extra_tax_rows(income: pd.DataFrame, default_pct: float = 0.0) -> pd.Series:
    """Extra tax to be paid on each Source row (rupees, positive = tax due; 0 where none applies).

    Only Dividend/FD, PL Booking and Miscellaneous rows can carry it. The rate is the % typed on the row (tax_pct); a row
    with no % uses the default % only when no tax has been entered on it (tax_paid empty or 0)."""
    amt = num(income["amount"])
    row_pct = num(income["tax_pct"]) if "tax_pct" in income else amt * 0
    has_row_pct = pd.to_numeric(income["tax_pct"], errors="coerce").notna() if "tax_pct" in income else amt.notna() & False
    taxed = num(income["tax_paid"]).abs() > 0 if "tax_paid" in income else amt.notna() & False
    pct = row_pct.where(has_row_pct, pd.Series(float(default_pct or 0.0), index=income.index).where(~taxed, 0.0))
    return (amt * pct / 100).where(income["source"].isin(db.EXTRA_TAX_BASE), 0.0)


def income_frame(income: pd.DataFrame, dividends: pd.DataFrame, realised: pd.DataFrame, include_auto=True,
                 premiums: pd.DataFrame | None = None, usd_inr: float | None = None, extra_tax_pct: float = 0.0) -> pd.DataFrame:
    """Unified income ledger: manual rows plus (optionally) stock dividends and realised P/L from the Stocks tab, and
    (optionally) the insurance premiums paid, negated."""
    amount = num(income["amount"])
    rsu_tax = None
    if "rsu_qty" in income:
        # RSUs: the shares sold for tax were turned into rupees on the sell date (fixed); the shares still held are
        # dollars, valued at today's USD->INR rate. Tax = shares sold x sell price x the sell-date rate.
        vested, buy = num(income["rsu_qty"]), num(income["rsu_buy_price"])
        sold = num(income["rsu_sold_qty"]) if "rsu_sold_qty" in income else vested * 0
        sold = pd.concat([sold, vested], axis=1).min(axis=1)          # cannot sell more than vested
        sell_px = num(income["rsu_sell_price"]) if "rsu_sell_price" in income else vested * 0
        sell_fx = num(income["rsu_sell_fx"]) if "rsu_sell_fx" in income else vested * 0
        sell_fx = sell_fx.where(sell_fx > 0, usd_inr or 0.0)          # no sell-date rate: use today's
        is_rsu = (income["source"] == db.RSU_SOURCE) & (vested > 0) & (buy > 0)
        if usd_inr:
            gross = (vested - sold) * buy * usd_inr + sold * buy * sell_fx
            amount = amount.where(~is_rsu, gross)
        tax_inr = (sold * sell_px * sell_fx).where(is_rsu, 0.0)
        sell_dt = pd.to_datetime(income["rsu_sell_date"], errors="coerce") if "rsu_sell_date" in income else None
        rsu_tax = pd.DataFrame({"date": income["date"] if sell_dt is None else
                                [s.date() if pd.notna(s) else d for s, d in zip(sell_dt, income["date"])],
                                "tax": tax_inr})
        rsu_tax = rsu_tax[rsu_tax["tax"] > 0]
    amount = amount.where(~income["source"].isin(db.DEDUCTION_SOURCES), -amount.abs())  # taxes reduce the total
    parts = [pd.DataFrame({"date": income["date"], "source": income["source"], "amount": amount,
                           "origin": "Source sheet"})]
    # taxes entered on an inflow's own row become a negative Taxes Paid row on its date. The "put in EPF/NPS or
    # returned" column (other_deductions) is information only and never subtracted.
    if rsu_tax is not None and len(rsu_tax):   # tax paid by selling shares at vest, on the sell date
        parts.append(pd.DataFrame({"date": rsu_tax["date"], "source": db.TAX_SOURCE, "amount": -rsu_tax["tax"],
                                   "origin": "Source sheet (RSU shares sold for tax)"}))
    if "tax_paid" in income:
        ded = num(income["tax_paid"])
        keep = ded.abs() > 0
        parts.append(pd.DataFrame({"date": income["date"][keep], "source": db.TAX_SOURCE, "amount": -ded[keep].abs(),
                                   "origin": "Source sheet"}))
    due = extra_tax_rows(income, extra_tax_pct)     # extra tax to be paid, charged on the row's own date
    if (due.abs() > 0).any():
        keep = due.abs() > 0
        parts.append(pd.DataFrame({"date": income["date"][keep], "source": db.EXTRA_TAX_SOURCE, "amount": -due[keep],
                                   "origin": "Extra tax"}))
    if include_auto:
        if extra_tax_pct:       # dividends and sale profit from the Stocks tab have no tax entered: the default % applies
            for frame, col in ((dividends, "amount"), (realised, "pnl")):
                x = num(frame[col]) if len(frame) else pd.Series(dtype=float)
                if len(x):
                    parts.append(pd.DataFrame({"date": frame["date"], "source": db.EXTRA_TAX_SOURCE,
                                               "amount": -x * extra_tax_pct / 100, "origin": "Extra tax"}))
        parts.append(pd.DataFrame({"date": dividends["date"], "source": "Dividend/FD",
                                   "amount": num(dividends["amount"]), "origin": "Stocks (dividend)"}))
        parts.append(pd.DataFrame({"date": realised["date"], "source": "PL Booking", "amount": realised["pnl"],
                                   "origin": "Stocks (sale)"}))
    if premiums is not None and len(premiums):     # insurance premiums paid reduce income, like taxes
        parts.append(pd.DataFrame({"date": premiums["date"], "source": db.PREMIUM_SOURCE,
                                   "amount": -num(premiums["amount"]).abs(), "origin": "Insurance"}))
    df = pd.concat([p for p in parts if len(p)], ignore_index=True) if any(len(p) for p in parts) else \
        pd.DataFrame(columns=["date", "source", "amount", "origin"])
    dt = pd.to_datetime(df["date"], errors="coerce")
    df["year"] = dt.dt.year
    df["month"] = dt.dt.month
    return df.dropna(subset=["year"]).astype({"year": int, "month": int})


def income_pivot(df: pd.DataFrame, year: int | None, by="month") -> pd.DataFrame:
    """Rows = income source, columns = month 1..12 (by='month', for `year`) or years (by='year')."""
    sources = db.INCOME_SOURCES + [db.EXTRA_TAX_SOURCE] + sorted(set(df["source"]) - set(db.INCOME_SOURCES) - {db.EXTRA_TAX_SOURCE})
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


def business_frame(b: pd.DataFrame) -> pd.DataFrame:
    """Businesses with their results. Net result = money made - money lost (the capital put in is not a loss);
    ROI = net result / invested. Overall = value now + made - lost - invested, when a value now is known (running)."""
    r = b.copy()
    for c in ("invested", "money_made", "money_lost", "current_value"):
        r[c] = num(r[c])
    r["net"] = r["money_made"] - r["money_lost"]
    r["roi_pct"] = (r["net"] / r["invested"].where(r["invested"] > 0)).fillna(0.0) * 100
    has_val = r["current_value"].notna() & (r["status"] == "Running")
    r["overall"] = (r["current_value"].fillna(0.0) + r["net"] - r["invested"]).where(has_val, r["net"] - r["invested"])
    start = pd.to_datetime(r["started_on"], errors="coerce")
    end = pd.to_datetime(r["closed_on"], errors="coerce").where(r["status"] == "Closed", pd.Timestamp(date.today()))
    r["months"] = ((end - start).dt.days / 30.4375).round(1)
    return r


# ---------------------------------------------------------------- insurance / loans / savings
def _ym(d) -> int:
    """A date as a running month number, so months can be compared and counted."""
    return d.year * 12 + d.month - 1


def _date_or_none(v):
    return None if v is None or (not hasattr(v, "year")) or pd.isna(v) else v


def _last_month(start, end):
    """The last calendar month a policy covers, as a month number. A policy that runs from 10 Mar to 9 Mar next year
    covers 12 months (Mar..Feb), not 13: the month of the day after `end` is left out when that day is on or before
    the start's day of the month."""
    from datetime import timedelta
    if end is None:
        return None
    nxt = end + timedelta(days=1)
    return _ym(nxt) - 1 if nxt.day <= (start.day if start else 1) else _ym(end)


def _policy_schedule(row, revisions: list) -> dict:
    """Month-by-month rules of one policy: is it in force, and what premium applies, in a given month.

    Each month costs  premium x payments-per-year / 12  (so a monthly 1,800 policy is 21,600 a year, and one in force
    for 6 months of a year costs 21,600 x 6 / 12 = 10,800 in that year). A premium change applies from the month of its
    effective date onward."""
    start, end = _date_or_none(row.get("start_date")), _date_or_none(row.get("end_date"))
    active = bool(row.get("active", True))
    last = _last_month(start, end)
    if row.get("frequency") == db.ONE_TIME and start and end and last >= _ym(start):
        mult = 12 / (last - _ym(start) + 1)       # one premium for the whole period: premium / months, a month at a time
    else:
        mult = db.FREQUENCIES.get(row.get("frequency"), 1)
    base = float(num(pd.Series([row.get("premium")])).iloc[0])
    revs = sorted((_ym(d), float(p)) for d, p in revisions)

    def in_force(ym):
        if not active and end is None:        # lapsed with no end date: unknown when, so it counts for nothing
            return False
        return not ((start and ym < _ym(start)) or (last is not None and ym > last))

    def premium_at(ym):
        prem = base
        for rym, p in revs:
            if rym <= ym:
                prem = p
        return prem

    step = None if row.get("frequency") == db.ONE_TIME else {1: 12, 2: 6, 4: 3, 12: 1}.get(mult, 12)
    return {"in_force": in_force, "premium_at": premium_at, "mult": mult, "start": start, "end": end, "last": last,
            "active": active, "step": step}


def _add_months(d, n):
    import calendar
    from datetime import date as _date
    ym = _ym(d) + n
    y, m = ym // 12, ym % 12 + 1
    return _date(y, m, min(d.day, calendar.monthrange(y, m)[1]))


def _payments(sch, today) -> list:
    """The premiums actually paid, as (date, amount): one on the start date and one every payment interval after it
    (monthly, quarterly, half-yearly, yearly), until today or the end date. A one-time premium is a single payment on
    the start date. The amount is the premium in force in the month of each payment."""
    start, end = sch["start"], sch["end"]
    if start is None or start > today or (not sch["active"] and end is None):
        return []
    limit = min(today, end) if end else today
    if sch["step"] is None:
        dates = [start] if start <= limit else []
    else:
        dates, k = [], 0
        while _add_months(start, k * sch["step"]) <= limit:
            dates.append(_add_months(start, k * sch["step"]))
            k += 1
    return [(d, sch["premium_at"](_ym(d))) for d in dates]


def _revisions_by_policy(revisions) -> dict:
    out: dict[int, list] = {}
    if revisions is not None and len(revisions):
        for _, r in revisions.iterrows():
            d = _date_or_none(r["effective_from"])
            if d is not None and pd.notna(r["premium"]):
                out.setdefault(int(r["insurance_id"]), []).append((d, r["premium"]))
    return out


def insurance_monthly(ins: pd.DataFrame, revisions: pd.DataFrame | None, year: int) -> pd.DataFrame:
    """Premium cost of every policy in every month of `year` (rows = policies, columns = 1..12), annualised:
    a policy paid yearly or quarterly still spreads its cost evenly over the months it is in force."""
    by_policy = _revisions_by_policy(revisions)
    out = pd.DataFrame(0.0, index=ins.index, columns=range(1, 13))
    for idx, row in ins.iterrows():
        sch = _policy_schedule(row, by_policy.get(int(row["id"]) if "id" in row and pd.notna(row["id"]) else -1, []))
        for m in range(1, 13):
            ym = year * 12 + m - 1
            if sch["in_force"](ym):
                out.loc[idx, m] = sch["premium_at"](ym) * sch["mult"] / 12
    return out


def insurance_payments(ins: pd.DataFrame, revisions: pd.DataFrame | None = None, today=None) -> pd.DataFrame:
    """Every premium actually paid, one row per payment on its date (a yearly premium is one row in the month it is
    paid, a monthly one a row every month). Policies with no start date have no known history and give no rows."""
    from datetime import date as _date
    today = today or _date.today()
    by_policy = _revisions_by_policy(revisions)
    rows = []
    for _, row in ins.iterrows():
        pid = int(row["id"]) if "id" in row and pd.notna(row["id"]) else -1
        sch = _policy_schedule(row, by_policy.get(pid, []))
        rows += [{"date": d, "policy": row.get("name"), "amount": amt} for d, amt in _payments(sch, today)]
    return pd.DataFrame(rows, columns=["date", "policy", "amount"])


def insurance_frame(ins: pd.DataFrame, revisions: pd.DataFrame | None = None, year: int | None = None,
                    today=None) -> pd.DataFrame:
    """Policies with their premium worked out per month, per year and over time.

    per_month / per_year: the premium rate being paid now (revisions applied), zero once lapsed or ended.
    year_cost / months_in_year: what the policy costs in `year` (default this year), pro-rated by the months in force.
    paid_to_date: the premiums actually paid from the start date until today (or the end date), each on its payment
    date (a yearly premium is paid in full at the start of each year); NaN when no start date was entered."""
    from datetime import date as _date
    today = today or _date.today()
    year = year or today.year
    i = ins.copy()
    if "start_date" not in i:
        i["start_date"] = None
    if "end_date" not in i:
        i["end_date"] = None
    if "active" not in i:
        i["active"] = True
    i["premium"] = num(i["premium"])
    monthly = insurance_monthly(i, revisions, year)
    i["months_in_year"] = (monthly > 0).sum(axis=1)
    i["year_cost"] = monthly.sum(axis=1)

    by_policy = _revisions_by_policy(revisions)
    now, rows = _ym(today), []
    for idx, row in i.iterrows():
        sch = _policy_schedule(row, by_policy.get(int(row["id"]) if "id" in row and pd.notna(row["id"]) else -1, []))
        if not bool(row["active"]) or (sch["end"] is not None and sch["end"] < today):   # real dates, not months
            status = "Inactive"
        elif sch["start"] is not None and sch["start"] > today:
            status = "Not started"
        else:
            status = "Active"
        prem_now = sch["premium_at"](now)
        per_year = prem_now * sch["mult"] if status == "Active" else 0.0   # a renewal that has not begun adds nothing yet
        if sch["start"] is not None:
            months = [m for m in range(_ym(sch["start"]), now + 1) if sch["in_force"](m)] \
                if sch["start"] <= today else []
            paid, n = sum(a for _, a in _payments(sch, today)), len(months)
        else:
            paid, n = float("nan"), float("nan")
        rows.append((prem_now, per_year, per_year / 12, paid, n, status))
    (i["premium_now"], i["per_year"], i["per_month"], i["paid_to_date"], i["months_to_date"],
     i["status"]) = zip(*rows) if rows else ([],) * 6
    return i


OPTIONAL_LOAN_COLS = ("installments_paid", "down_payment", "other_charges", "emi_paid", "emi_interest",
                      "closing_payment")


def whole_months(a, b):
    """Completed months from date a to date b (None when either is missing); a month completes on the same day
    number, or on the last day of a shorter month (30 Jan to 29 Feb is one month; 15 Jan to 14 Feb is zero)."""
    import calendar
    try:
        if a is None or b is None or pd.isna(a) or pd.isna(b):
            return None
        m = (b.year - a.year) * 12 + (b.month - a.month)
        if b.day < a.day and b.day != calendar.monthrange(b.year, b.month)[1]:
            m -= 1
        return max(0, m)
    except AttributeError:
        return None


def emi_schedule(principal: float, rate_pct: float, planned_months: int, months_paid: int) -> dict | None:
    """Standard reducing-balance EMI maths for a loan that is paid off after `months_paid` EMIs.

    Returns the EMI, the interest the full tenure would have cost, the interest actually paid (EMIs paid plus the
    balance cleared on closing) and the difference. None when the inputs cannot support it."""
    try:
        P, n, k = float(principal), int(planned_months), int(months_paid)
        r = float(rate_pct) / 1200.0
    except (TypeError, ValueError):
        return None
    if not (P > 0 and n > 0 and r >= 0) or any(pd.isna(v) for v in (P, r)):
        return None
    k = max(0, min(k, n))
    emi = P / n if r == 0 else P * r * (1 + r) ** n / ((1 + r) ** n - 1)
    balance = P * (1 + r) ** k - emi * (((1 + r) ** k - 1) / r if r else k)   # still owed after k EMIs
    balance = max(balance, 0.0)
    full = emi * n - P
    paid = emi * k + balance - P
    return {"emi": emi, "interest_full": full, "interest_paid": paid, "interest_saved": max(full - paid, 0.0),
            "balance_cleared": balance}


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

    l["elapsed_months"] = [whole_months(a, b) for a, b in zip(l["start_date"], end)]
    # tenure = installments actually paid when recorded (the first EMI usually falls 1-2 months after disbursal),
    # otherwise the whole months between the start and closing (or planned end) date
    for c in OPTIONAL_LOAN_COLS:            # "not recorded" must stay NaN, never become 0
        l[c] = pd.to_numeric(l[c], errors="coerce") if c in l else float("nan")
    l["elapsed_months"] = pd.to_numeric(l["elapsed_months"], errors="coerce")
    l["tenure_months"] = l["installments_paid"].where(l["installments_paid"].notna(), l["elapsed_months"])
    l["planned_months"] = [whole_months(a, b) for a, b in zip(l["start_date"], l["end_date"])]
    l["other_charges"] = l["other_charges"].fillna(0.0)
    # what the borrower actually paid, when it was recorded
    l["asset_cost"] = (l["down_payment"].fillna(0.0) + l["principal"]).where(l["principal"] > 0)
    l["emi_principal"] = l["emi_paid"] - l["emi_interest"]
    est = ("emi_std", "interest_full", "interest_est", "interest_saved", "balance_est")
    for c in est:
        l[c] = float("nan")
    for i, r in l.iterrows():
        k = r["tenure_months"] if closed[i] else 0
        res = emi_schedule(r["principal"], r["interest_rate"], r["planned_months"], k if pd.notna(k) else None)
        if res:
            l.loc[i, ["emi_std", "interest_full"]] = [res["emi"], res["interest_full"]]
            if closed[i] and pd.notna(k):
                l.loc[i, ["interest_est", "balance_est"]] = [res["interest_paid"], res["balance_cleared"]]
    # actuals win over the calculator estimate
    l["interest_paid"] = l["emi_interest"].where(l["emi_interest"].notna(), l["interest_est"])
    l["balance_cleared"] = (l["principal"] - l["emi_principal"]).where(l["emi_principal"].notna(), l["balance_est"])
    l.loc[~closed, ["balance_cleared"]] = float("nan")
    l["cost_of_borrowing"] = (l["interest_paid"] + l["other_charges"]).where(l["interest_paid"].notna())
    l["interest_saved"] = (l["interest_full"] - l["interest_paid"]).clip(lower=0).where(closed)
    l["net_saved"] = l["interest_saved"] - l["other_charges"]          # saving after the charges you paid
    l["total_paid"] = (l["down_payment"].fillna(0.0) + l["emi_paid"] + l["closing_payment"]).where(
        l["emi_paid"].notna() & l["closing_payment"].notna() & closed)
    l["lender_paid"] = (l["emi_paid"] + l["closing_payment"]).where(
        l["emi_paid"].notna() & l["closing_payment"].notna() & closed)   # everything paid to the lender, no down payment
    l["lender_extra"] = l["lender_paid"] - l["principal"]               # = interest + other charges when it all adds up
    # does the closing payment equal the balance plus the charges? (difference, 0 when it does)
    l["closing_gap"] = (l["closing_payment"] - l["balance_cleared"] - l["other_charges"]).where(
        l["closing_payment"].notna() & l["balance_cleared"].notna())
    return l


def loan_payments(loans: pd.DataFrame, today=None) -> pd.DataFrame:
    """What borrowing cost, one row per payment, for the Summary's "where the income went".

    Only the cost of the loan counts: the interest and the other charges (processing fee, foreclosure charges). The
    down payment and the principal repaid are not loan costs: the money bought something (an asset that is recorded on
    its own tab, or spending that is already part of what is left over).
    The loan sheet records totals, not every instalment, so the dates are worked out:
      * the interest paid (recorded, else the calculator's figure) is spread over the instalments, one a month from the
        month after the start date (as many as were paid, or the whole months elapsed for a running loan, never past
        the planned tenure and never after the loan was closed);
      * other charges fall on the closing date of a closed loan (they are part of the closing payment), else the start.
    Columns: date, loan, kind, amount."""
    from datetime import date as _date
    today = today or _date.today()
    cols = ["date", "loan", "kind", "amount"]
    if loans is None or loans.empty:
        return pd.DataFrame(columns=cols)
    lf = loans_frame(loans)
    rows = []
    for _, r in lf.iterrows():
        start, name = _date_or_none(r["start_date"]), r["name"]
        if start is None:
            continue
        closed = r["status"] == "Closed"
        stop = _date_or_none(r.get("closed_date")) if closed else None
        n = r["installments_paid"]
        if pd.isna(n):
            n = r["tenure_months"] if closed else whole_months(start, today)
            if not closed and pd.notna(r["planned_months"]):
                n = min(n or 0, r["planned_months"])
        n = int(n) if n is not None and pd.notna(n) and n > 0 else 0
        interest = r["interest_paid"]                 # recorded, else the calculator's figure for a closed loan
        if pd.isna(interest) and n:                   # a running loan: the calculator's interest after n instalments
            res = emi_schedule(r["principal"], r["interest_rate"], r["planned_months"], n)
            interest = res["interest_paid"] if res else float("nan")   # interest accrued over those n months
        if pd.notna(interest) and interest > 0:
            dates = []
            for k in range(1, n + 1):
                d = _add_months(start, k)
                dates.append(min(d, stop) if stop and d > stop else d)
            if not dates:
                dates = [stop or start]
            for d in dates:
                if d <= today:
                    rows.append((d, name, "Interest", float(interest) / len(dates)))
        if pd.notna(r["other_charges"]) and r["other_charges"] > 0:
            d = stop if (closed and stop) else start
            if d <= today:
                rows.append((d, name, "Other charges", float(r["other_charges"])))
    return pd.DataFrame(rows, columns=cols)


def invested_shares(positions: pd.DataFrame) -> pd.DataFrame:
    """Adds invested_pct: each holding's invested amount as a % of the total invested in the whole portfolio (open
    holdings only; a fully sold holding has nothing invested, so 0)."""
    p = positions.copy()
    inv = num(p["invested"]).where(num(p["qty"]) > 0, 0.0)
    total = inv.sum()
    p["invested_pct"] = (inv / total * 100) if total > 0 else 0.0
    return p


def category_summary(positions: pd.DataFrame) -> pd.DataFrame:
    """One row per category with open holdings: holdings, invested, % of the total invested, current value, unrealised
    P/L, and the share of the portfolio's current value. Highest invested first."""
    p = invested_shares(positions)
    p = p[num(p["qty"]) > 0]
    if p.empty:
        return pd.DataFrame(columns=["category", "holdings", "invested", "invested_pct", "value", "pnl", "pnl_pct"])
    g = p.groupby("category").agg(holdings=("name", "size"), invested=("invested", "sum"),
                                  invested_pct=("invested_pct", "sum"), value=("value", "sum")).reset_index()
    g["pnl"] = g["value"] - g["invested"]
    g["pnl_pct"] = (g["pnl"] / g["invested"].where(g["invested"] > 0) * 100)
    return g.sort_values("invested", ascending=False, ignore_index=True)


def active_savings(sv: pd.DataFrame) -> pd.DataFrame:
    """Savings rows that count today: an FD (or other deposit) marked temporarily closed is taken out of its category
    until it is added back, so it is left out of every total."""
    if "temp_closed" not in sv:
        return sv
    return sv[~sv["temp_closed"].fillna(False).astype(bool)]


def temporarily_closed(sv: pd.DataFrame) -> pd.DataFrame:
    """The rows marked temporarily closed (money that is out of its category for now and meant to go back)."""
    if "temp_closed" not in sv:
        return sv.iloc[0:0]
    return sv[sv["temp_closed"].fillna(False).astype(bool)]


def deposit_status(row, today=None) -> str:
    """Status of a deposit: Temporarily closed, Matured (renewed / not renewed), Active, or '' for open-ended money."""
    from datetime import date as _date
    today = today or _date.today()
    if bool(row.get("temp_closed")):
        return "Temporarily closed"
    mat = row.get("maturity_date")
    if mat is None or pd.isna(mat) or not hasattr(mat, "year"):
        return "Active" if row.get("kind") in db.TERM_KINDS else ""
    if mat < today:
        return "Matured, renewed" if bool(row.get("auto_renew")) else "Matured"
    return "Active"


def savings_totals(sv: pd.DataFrame) -> pd.DataFrame:
    s = active_savings(sv).copy()
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
        rows.append((db.PLAN_GROUP.get(b, b), p["value"].sum(), p["invested"].sum()))
    for m in db.METALS:
        x = metals[metals["metal"] == m]
        rows.append((db.PLAN_GROUP.get(m, m), x["value"].sum(), x["invested"].sum()))
    rows.append(("Real Estate", real_estate["value"].sum(), real_estate["total_cost"].sum()))
    rows.append((db.SAVINGS_BUCKET, savings_total, savings_total))
    out = pd.DataFrame(rows, columns=["bucket", "value", "invested"])
    return out.groupby("bucket", sort=False, as_index=False).sum()


def target_plan(main: dict, subs: pd.DataFrame, income: float) -> pd.DataFrame:
    """The ideal plan as one table. A main row per category (its % of income) followed by its sub-category rows.

    pct_of_category: the % of the category's share given to the sub-category.
    pct_of_income:   main % x sub % / 100, i.e. a share of the whole income (40% investment, 50% of it in mutual funds
                     -> 20% of income). amount = income x pct_of_income.
    A category whose sub-categories add up to less than 100% gets an 'Not divided yet' row for the rest."""
    rows = []
    for cat in db.MAIN_CATEGORIES:
        m = float(main.get(cat, 0.0) or 0.0)
        part = subs[subs["category"] == cat] if len(subs) else subs
        if not m and part.empty:
            continue
        rows.append({"category": cat, "sub": None, "pct_of_category": 100.0 if m else 0.0, "pct_of_income": m,
                     "amount": income * m / 100})
        used = 0.0
        for _, r in part.iterrows():
            p = float(r["pct"] or 0.0)
            used += p
            rows.append({"category": cat, "sub": r["sub"], "pct_of_category": p, "pct_of_income": m * p / 100,
                         "amount": income * m * p / 10000})
        if m and 0 < used < 100 - 1e-9:
            rows.append({"category": cat, "sub": "Not divided yet", "pct_of_category": 100 - used,
                         "pct_of_income": m * (100 - used) / 100, "amount": income * m * (100 - used) / 10000})
    return pd.DataFrame(rows, columns=["category", "sub", "pct_of_category", "pct_of_income", "amount"])


def main_actuals(flow: dict) -> dict[str, float | None]:
    """What really went into each main category in the period, from money_flow. Business & Trading is the money put into the
    businesses on the Business tab; Expenses includes insurance premiums and leisure (they are not tracked apart)."""
    return {"Savings": flow["saved"], "Investment": flow["stocks"] + flow["metals"] + flow["pf"], "Real-Estate": flow["real_estate"],
            "Business & Trading": flow["business"], "EMI": flow["loans"], "Expenses & Leisure": flow["expense"] + flow["insurance"]}


def net_worth(invest_value: float, savings_total: float, loans_outstanding: float) -> float:
    return invest_value + savings_total - loans_outstanding


def money_flow(income_total: float, trades_flow: dict, metals: pd.DataFrame, real_estate: pd.DataFrame,
               savings: pd.DataFrame, year: int | None = None, insurance_paid: pd.DataFrame | None = None,
               loan_paid: pd.DataFrame | None = None, business: pd.DataFrame | None = None) -> dict:
    """Where the income went in a period (a year, or all time when year is None).

        Expense = Income - Investments - Savings - Insurance - Loans

    Insurance = premiums actually paid in the period (insurance_paid: date, amount). Loans = cash paid on loans in the
    period (loan_paid: date, amount): down payments, instalments and closing payments.

    Investments = new money put into assets in the period: cost of stock/fund buys minus the cost basis of units
    sold (so recycled capital is not counted twice; profit from sells is already in income), gold/silver bought
    (incl. charges) and property bought (incl. charges), by purchase date. Savings = cash/FD/RD/PPF/goal balances;
    EPF and NPS are invested into funds, so they count as Investments (they are still entered under Cash & Savings);
    for a single year only those with a start date in that year can be attributed to it.
    """
    def in_year(series):
        d = pd.to_datetime(series, errors="coerce")
        return d.dt.year == year if year is not None else pd.Series(True, index=series.index)

    m = metals[in_year(metals["date"])]["invested"].sum() if len(metals) else 0.0
    r = real_estate[in_year(real_estate["purchase_date"])]["total_cost"].sum() if len(real_estate) else 0.0
    sv = active_savings(savings)      # a temporarily closed FD is not counted until it is added back
    if year is not None and len(sv):
        sv = sv[in_year(sv["start_date"])]
    pf_mask = sv["kind"].isin(db.PF_KINDS) if "kind" in sv else pd.Series(False, index=sv.index)
    pf = float(num(sv.loc[pf_mask, "amount"]).sum()) if len(sv) else 0.0        # EPF / NPS: invested, not saved
    saved = float(num(sv.loc[~pf_mask, "amount"]).sum()) if len(sv) else 0.0
    stocks = float(trades_flow.get(year if year is not None else "all", 0.0))
    biz = 0.0
    if business is not None and len(business):       # capital put into businesses, by the date they started
        bz = business[in_year(business["started_on"])]
        biz = float(num(bz["invested"]).sum())
    invested = stocks + m + r + biz + pf

    def paid(frame):
        if frame is None or not len(frame):
            return 0.0
        f = frame[in_year(frame["date"])]
        return float(num(f["amount"]).sum())

    insurance_total, loans_paid = paid(insurance_paid), paid(loan_paid)
    return {"income": income_total, "stocks": stocks, "metals": float(m), "real_estate": float(r), "business": biz, "pf": pf,
            "invested": invested, "saved": saved, "insurance": insurance_total, "loans": loans_paid,
            "expense": income_total - invested - saved - insurance_total - loans_paid,
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


def _txt(v) -> str:
    """Text cell, '' when it is missing (None or NaN)."""
    return v if isinstance(v, str) else ""


def loans_markdown(loans: pd.DataFrame, title="Loan summary") -> str:
    """A read-only, shareable write-up of the loans (hand it to a partner, advisor or family member)."""
    l = loans_frame(loans)
    lines = [f"# {title}", "", f"Total outstanding: {inr(l['outstanding'].sum())} · "
             f"Total EMI per month: {inr(l['emi'].sum())}", ""]
    for _, r in l.iterrows():
        lines += [f"## {r['name']}  ({r['category']} debt)", "",
                  f"- Lender: {_txt(r['lender']) or '–'}",
                  f"- Principal: {inr(r['principal'])} · Outstanding: {inr(r['outstanding'])} "
                  f"({r['repaid_pct']:.0f}% repaid)",
                  f"- Status: {r['status']}"
                  + (f" (closed {r['closed_date']})" if r['status'] == 'Closed' and r['closed_date'] else "")
                  + (f" · Tenure: {int(r['tenure_months'])} months" if pd.notna(r['tenure_months']) else ""),
                  f"- Interest rate: {r['interest_rate']:g}% · EMI: {inr(r['emi'])}"]
        if r["status"] == "Closed" and pd.notna(r["interest_paid"]):
            lines.append(f"- Interest paid: {inr(r['interest_paid'])} · Other charges: {inr(r['other_charges'])} · "
                         f"Cost of borrowing: {inr(r['cost_of_borrowing'])}")
            if pd.notna(r["interest_saved"]):
                lines.append(f"- Full-tenure interest would have been {inr(r['interest_full'])}; saved "
                             f"{inr(r['interest_saved'])} by closing early ({inr(r['net_saved'])} after charges)")
        lines += [
                  f"- Start: {r['start_date'] or '–'} · End: {r['end_date'] or '–'}"]
        if _txt(r["link"]):
            lines.append(f"- Documents: {_txt(r['link'])}")
        if _txt(r["details"]):
            lines += ["", _txt(r["details"])]
        lines.append("")
    return "\n".join(lines)


def memo_pivot(income: pd.DataFrame, year: int | None, by="month", col="other_deductions") -> pd.Series:
    """One row for the Source table: the amount in `col` of the raw income rows (EPF / NPS / returned), per month of
    `year` (by='month') or per year (by='year'). It is recorded only, so it is kept out of every total."""
    dt = pd.to_datetime(income["date"], errors="coerce")
    amt = num(income[col]) if col in income else pd.Series(0.0, index=income.index)
    if by == "month":
        s = amt[dt.dt.year == year].groupby(dt[dt.dt.year == year].dt.month).sum()
        return s.reindex(range(1, 13), fill_value=0.0)
    return amt[dt.notna()].groupby(dt[dt.notna()].dt.year).sum()


def average_growth(yoy: pd.Series) -> float | None:
    """Mean of the year-on-year growth percentages that exist (None when there are none)."""
    s = pd.to_numeric(yoy, errors="coerce").dropna()
    return float(s.mean()) if len(s) else None


def growth_line(net: pd.Series, avg_pct: float | None) -> pd.Series:
    """Where income would be if it grew by `avg_pct` every year from its first positive year (NaN before it)."""
    out = pd.Series(float("nan"), index=net.index)
    pos = net[net > 0]
    if avg_pct is None or pos.empty:
        return out
    first = pos.index[0]
    for i, k in enumerate([k for k in net.index if k >= first]):
        out[k] = pos.iloc[0] * (1 + avg_pct / 100) ** i
    return out


def rsu_table(income: pd.DataFrame, usd_inr: float | None) -> pd.DataFrame:
    """One row per RSU vest: shares vested, buy price, shares sold for tax (price, date, rate), tax in rupees, shares
    still held, and what the held shares are worth at today's rate (vs their buy value at the sell-date/vest rate)."""
    cols = ["date", "description", "vested", "buy_price", "sold", "sell_price", "sell_date", "sell_fx", "tax_inr",
            "held", "held_usd", "held_inr_now"]
    if "rsu_qty" not in income:
        return pd.DataFrame(columns=cols)
    vested, buy = num(income["rsu_qty"]), num(income["rsu_buy_price"])
    r = income[(income["source"] == db.RSU_SOURCE) & (vested > 0) & (buy > 0)]
    v, b = vested[r.index], buy[r.index]
    sold = num(r["rsu_sold_qty"]) if "rsu_sold_qty" in r else v * 0
    sold = pd.concat([sold, v], axis=1).min(axis=1)
    px = num(r["rsu_sell_price"]) if "rsu_sell_price" in r else v * 0
    fx = num(r["rsu_sell_fx"]) if "rsu_sell_fx" in r else v * 0
    fx = fx.where(fx > 0, usd_inr or 0.0)
    out = pd.DataFrame({"date": r["date"], "description": r["description"], "vested": v, "buy_price": b, "sold": sold,
                        "sell_price": px, "sell_date": r["rsu_sell_date"] if "rsu_sell_date" in r else None,
                        "sell_fx": fx, "tax_inr": sold * px * fx, "held": v - sold, "held_usd": (v - sold) * b})
    out["held_inr_now"] = out["held_usd"] * usd_inr if usd_inr else float("nan")
    return out


def inflow_by_year(df: pd.DataFrame) -> pd.DataFrame:
    """Cash inflow per year: before tax (gross), tax paid, after tax (net), and each one's year-on-year change (%):
    gross_yoy_pct, tax_yoy_pct and yoy_pct (after tax).
    `df` is an income_frame. YoY is NaN for the first year or when the previous year was zero or negative."""
    if df.empty:
        return pd.DataFrame(columns=["year", "gross", "tax", "net", "yoy_pct", "gross_yoy_pct", "tax_yoy_pct"])
    g = df.groupby("year").apply(lambda d: pd.Series({
        "gross": d.loc[~d["source"].isin([db.TAX_SOURCE, db.EXTRA_TAX_SOURCE]), "amount"].sum(),
        "tax": -d.loc[d["source"].isin([db.TAX_SOURCE, db.EXTRA_TAX_SOURCE]), "amount"].sum()}), include_groups=False)
    g["net"] = g["gross"] - g["tax"]
    g = g.reindex(range(int(g.index.min()), int(g.index.max()) + 1), fill_value=0.0)
    for col, name in (("net", "yoy_pct"), ("gross", "gross_yoy_pct"), ("tax", "tax_yoy_pct")):
        prev = g[col].shift(1)
        g[name] = ((g[col] - prev) / prev * 100).where(prev > 0)
    return g.rename_axis("year").reset_index()
