"""Checks for the editable tables: what is wrong with an edit, said in plain words, row by row.

`validate(table, df, required)` returns a list of messages (empty = fine). The app shows them and saves nothing, so a
bad edit is never silently dropped."""
from __future__ import annotations

from datetime import date

import pandas as pd

import db

DATE_MIN, DATE_MAX = date(1995, 1, 1), date(2100, 12, 31)

LABELS = {
    "name": "Name", "date": "Date", "source": "Section", "amount": "Amount", "kind": "Type", "category": "Category",
    "currency": "Currency", "current_price": "Price now", "side": "Side", "qty": "Units", "price": "Price",
    "fees": "Fees", "fx_rate": "USD→INR rate", "security_id": "Holding", "premium": "Premium",
    "frequency": "Paid", "sum_assured": "Sum assured", "start_date": "Start date", "end_date": "End date",
    "next_due": "Next due", "effective_from": "Effective from", "insured_for": "Insured", "type": "Type",
    "principal": "Loan amount", "outstanding": "Outstanding", "interest_rate": "Interest %", "emi": "EMI",
    "status": "Status", "closed_date": "Closed on", "down_payment": "Down payment", "other_charges": "Other charges",
    "installments_paid": "Installments paid", "emi_paid": "Paid through installments",
    "emi_interest": "Interest in installments", "closing_payment": "Paid to close", "metal": "Metal",
    "purity": "Purity", "grams": "Grams", "rate_per_g": "Rate per gram", "making_charges": "Making charges",
    "gst": "GST", "cost": "Cost", "area_sqft": "Area", "registration": "Registration", "brokerage": "Brokerage",
    "monthly_rent": "Rent per month", "current_per_sqft": "Current price per sq ft", "purchase_date": "Purchased on",
    "maturity_date": "Maturity date", "target": "Goal target", "title": "Title", "description": "Description",
    "price_date": "Price as of", "tax_paid": "Taxes paid", "other_deductions": "EPF / NPS / returned", "rsu_qty": "Shares vested", "rsu_buy_price": "Buy price", "rsu_sold_qty": "Shares sold for tax",
    "rsu_sell_price": "Sell price", "rsu_sell_date": "Sell date", "rsu_sell_fx": "USD to INR on the sell date",
}

# column -> rule for each table. Rules: (">", 0) must be above 0, (">=", 0) not negative, ("between", lo, hi),
# ("in", options), ("int",) whole number
RULES: dict[str, dict[str, tuple]] = {
    "income": {"source": ("in", db.INCOME_SOURCES), "tax_paid": (">=", 0), "other_deductions": (">=", 0), "rsu_qty": (">=", 0), "rsu_buy_price": (">=", 0),
               "rsu_sold_qty": (">=", 0), "rsu_sell_price": (">=", 0), "rsu_sell_fx": (">=", 0)},
    "savings": {"kind": ("in", db.SAVINGS_KINDS), "amount": (">=", 0), "interest_rate": ("between", 0, 100),
                "target": (">=", 0)},
    "insurance": {"premium": (">", 0), "sum_assured": (">=", 0), "frequency": ("in", db.FREQUENCY_OPTIONS),
                  "insured_for": ("in", db.INSURED_FOR), "type": ("in", db.INSURANCE_TYPES)},
    "insurance_revisions": {"premium": (">", 0)},
    "loans": {"category": ("in", db.LOAN_CATEGORIES), "status": ("in", db.LOAN_STATUSES),
              "principal": (">=", 0), "outstanding": (">=", 0), "down_payment": (">=", 0), "other_charges": (">=", 0),
              "emi": (">=", 0), "emi_paid": (">=", 0), "emi_interest": (">=", 0), "closing_payment": (">=", 0),
              "interest_rate": ("between", 0, 100), "installments_paid": (">=", 0)},
    "securities": {"category": ("in", db.SECURITY_CATEGORIES), "currency": ("in", db.CURRENCIES),
                   "current_price": (">=", 0)},
    "trades": {"side": ("in", ["Buy", "Sell"]), "qty": (">", 0), "price": (">", 0), "fees": (">=", 0),
               "fx_rate": (">", 0)},
    "dividends": {"amount": (">", 0)},
    "metals": {"metal": ("in", db.METALS), "grams": (">", 0), "rate_per_g": (">", 0), "making_charges": (">=", 0),
               "gst": (">=", 0), "other_charges": (">=", 0)},
    "real_estate": {"cost": (">", 0), "area_sqft": (">=", 0), "registration": (">=", 0), "brokerage": (">=", 0),
                    "other_charges": (">=", 0), "monthly_rent": (">=", 0), "current_per_sqft": (">=", 0)},
}

# (earlier date column, later date column): the later one may not come before the earlier one
ORDERED_DATES = {"savings": [("start_date", "maturity_date")], "insurance": [("start_date", "end_date")],
                 "loans": [("start_date", "end_date"), ("start_date", "closed_date")]}


def _blank(v) -> bool:
    if v is None or v is pd.NaT:
        return True
    if isinstance(v, str):
        return not v.strip()
    try:
        return bool(pd.isna(v))
    except (TypeError, ValueError):
        return False


def _label(col: str) -> str:
    return LABELS.get(col, col.replace("_", " ").capitalize())


def _row_name(row, pos: int) -> str:
    for col in ("name", "title", "description", "form", "kind"):
        if col in row and not _blank(row[col]):
            return f"Row {pos} ({row[col]})"
    return f"Row {pos}"


def _as_date(v):
    try:
        return pd.to_datetime(v).date()
    except (TypeError, ValueError):
        return None


def _num(v):
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _shown(v) -> str:
    return f"{v:g}" if isinstance(v, (int, float)) else str(v)


def validate(table: str, df: pd.DataFrame, required=()) -> list[str]:
    errors: list[str] = []
    cols, date_cols, _flags = db.TABLES[table]
    rules = RULES.get(table, {})
    for pos, (_, row) in enumerate(df.iterrows(), start=1):
        who = _row_name(row, pos)

        def bad(msg):
            errors.append(f"{who}: {msg}")

        for col in required:
            if col in row and _blank(row[col]):
                bad(f"{_label(col)} is required.")
        for col, rule in rules.items():
            if col not in row or _blank(row[col]):
                continue
            v, kind = row[col], rule[0]
            if kind == "in":
                if v not in rule[1]:
                    bad(f"{_label(col)} “{v}” is not one of: {', '.join(map(str, rule[1]))}.")
                continue
            n = _num(v)
            if n is None:
                bad(f"{_label(col)} must be a number (got “{v}”).")
            elif kind == ">" and not n > rule[1]:
                bad(f"{_label(col)} must be above {_shown(rule[1])} (got {_shown(n)}).")
            elif kind == ">=" and n < rule[1]:
                bad(f"{_label(col)} cannot be negative (got {_shown(n)}).")
            elif kind == "between" and not rule[1] <= n <= rule[2]:
                bad(f"{_label(col)} must be between {_shown(rule[1])} and {_shown(rule[2])} (got {_shown(n)}).")
        if table == "income" and not _blank(row.get("rsu_sold_qty")) and (_num(row.get("rsu_sold_qty")) or 0) > 0:
            if (_num(row.get("rsu_sold_qty")) or 0) > (_num(row.get("rsu_qty")) or 0):
                bad("Shares sold for tax cannot be more than the shares vested.")
            if _blank(row.get("rsu_sell_price")) or not (_num(row.get("rsu_sell_price")) or 0) > 0:
                bad("Shares sold for tax need a sell price above 0.")
        if table == "income" and not _blank(row.get("amount")):
            n = _num(row["amount"])
            if n == 0:
                bad("Amount cannot be zero.")
            elif n is not None and n < 0 and row.get("source") in db.DEDUCTION_SOURCES:
                bad("A Taxes Paid row is entered as a positive amount (it is subtracted for you). "
                    "Use a negative amount under another source for a loss.")
        if table == "loans" and not _blank(row.get("installments_paid")):
            n = _num(row["installments_paid"])
            if n is not None and n != int(n):
                bad(f"{_label('installments_paid')} must be a whole number (got {_shown(n)}).")
        if table == "loans" and not _blank(row.get("emi_interest")) and not _blank(row.get("emi_paid")):
            if _num(row["emi_interest"]) > _num(row["emi_paid"]):
                bad("Interest in the installments cannot be more than the installments paid.")
        if table == "metals" and not _blank(row.get("purity")) and not _blank(row.get("metal")):
            ok = db.PURITIES.get(row["metal"], [])
            if row["metal"] in db.PURITIES and str(row["purity"]) not in ok:
                bad(f"Purity “{row['purity']}” does not exist for {row['metal']} (use {', '.join(ok)}).")
        # dates must be real dates in a sane range
        dates = {}
        for col in date_cols:
            if col not in row or _blank(row[col]):
                continue
            d = _as_date(row[col])
            if d is None:
                bad(f"{_label(col)} is not a valid date (got “{row[col]}”).")
            elif not DATE_MIN <= d <= DATE_MAX:
                bad(f"{_label(col)} {d} is outside {DATE_MIN.year}-{DATE_MAX.year}.")
            else:
                dates[col] = d
        for first, later in ORDERED_DATES.get(table, []):
            if first in dates and later in dates and dates[later] < dates[first]:
                bad(f"{_label(later)} ({dates[later]}) is before {_label(first).lower()} ({dates[first]}).")
        if table == "insurance" and row.get("frequency") == db.ONE_TIME and not (
                not _blank(row.get("start_date")) and not _blank(row.get("end_date"))):
            bad(f"A “{db.ONE_TIME}” premium needs both a start and an end date.")
    return errors
