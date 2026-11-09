"""SQLite data layer for the Asset Tracker (single personal database)."""
from __future__ import annotations

import math
import os
import shutil
import sqlite3
from contextlib import contextmanager
from datetime import date, datetime

import pandas as pd

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("ASSET_DB", os.path.join(BASE_DIR, "data", "assets.db"))
BACKUP_DIR = os.environ.get("ASSET_BACKUP_DIR", os.path.join(BASE_DIR, "backups"))

# ---- fixed vocabularies -------------------------------------------------------------------------------
TAX_SOURCE = "Taxes Paid"  # entered as a positive amount, subtracted from income totals
PREMIUM_SOURCE = "Insurance Premium"  # derived from the Insurance tab, subtracted from income like taxes
INCOME_SOURCES = ["Salary", "Dividend/FD", "PL Booking", "Miscellaneous", TAX_SOURCE]
SECURITY_CATEGORIES = [
    "India - High Quality", "India - Risky", "Index Funds", "Mutual Funds", "ETFs",
    "International - Stable", "International - Risky", "REITs/InvITs", "Unlisted Stocks", "Crypto",
]
METALS = ["Gold", "Silver"]
PURITIES = {"Gold": ["24K", "22K", "21K", "20K", "18K", "14K"], "Silver": ["999", "925", "800"]}
DEFAULT_PURITY = {"Gold": "24K", "Silver": "999"}
# Starting local premium over the international price: India's 6% import duty. Calibrate it to your jeweller.
DEFAULT_PREMIUM = {"Gold": 6.0, "Silver": 6.0}
SAVINGS_BUCKET = "Cash & Savings"
INVESTMENT_BUCKETS = SECURITY_CATEGORIES + ["Gold", "Silver", "Real Estate", SAVINGS_BUCKET]
CURRENCIES = ["INR", "USD"]
SAVINGS_KINDS = ["Savings Account", "Cash in Hand", "FD", "RD", "EPF", "NPS", "PPF", "Short-term Goal"]
INSURED_FOR = ["Me", "Family", "Own Damage (OD)", "Third Party (TP)", "Other"]   # who or what a policy covers
INSURANCE_TYPES = ["Term Life", "Health", "Life/Endowment", "Vehicle", "Home", "Travel", "Other"]
FREQUENCIES = {"Monthly": 12, "Quarterly": 4, "Half-Yearly": 2, "Yearly": 1}
ONE_TIME = "One-time (whole period)"      # one premium that covers the whole start-to-end period (e.g. a 5-year TP)
FREQUENCY_OPTIONS = list(FREQUENCIES) + [ONE_TIME]
LOAN_CATEGORIES = ["Good", "Bad", "Worst"]
LOAN_STATUSES = ["Active", "Closed"]
NOTE_KINDS = ["Todo", "Idea", "Note"]

SCHEMA = """
CREATE TABLE IF NOT EXISTS income (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL, source TEXT NOT NULL, description TEXT, amount REAL NOT NULL);
CREATE TABLE IF NOT EXISTS securities (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL UNIQUE, symbol TEXT, category TEXT NOT NULL, reason TEXT,
    current_price REAL, price_date TEXT, currency TEXT DEFAULT 'INR');
CREATE TABLE IF NOT EXISTS trades (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id INTEGER NOT NULL, date TEXT NOT NULL,
    side TEXT NOT NULL CHECK (side IN ('Buy','Sell')),
    qty REAL NOT NULL CHECK (qty > 0), price REAL NOT NULL CHECK (price >= 0), fees REAL, note TEXT,
    fx_rate REAL);
CREATE TABLE IF NOT EXISTS dividends (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    security_id INTEGER NOT NULL, date TEXT NOT NULL, amount REAL NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS savings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    kind TEXT NOT NULL, name TEXT NOT NULL, amount REAL NOT NULL, purpose TEXT,
    interest_rate REAL, start_date TEXT, maturity_date TEXT, target REAL, note TEXT);
CREATE TABLE IF NOT EXISTS insurance (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, type TEXT, provider TEXT, policy_no TEXT, sum_assured REAL,
    premium REAL NOT NULL, frequency TEXT NOT NULL, next_due TEXT, link TEXT, notes TEXT,
    active INTEGER NOT NULL DEFAULT 1, start_date TEXT, end_date TEXT,
    insured_for TEXT);
CREATE TABLE IF NOT EXISTS insurance_revisions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    insurance_id INTEGER NOT NULL, effective_from TEXT NOT NULL, premium REAL NOT NULL, note TEXT);
CREATE TABLE IF NOT EXISTS loans (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, lender TEXT, category TEXT NOT NULL, principal REAL, outstanding REAL NOT NULL,
    interest_rate REAL, emi REAL, start_date TEXT, end_date TEXT, link TEXT, details TEXT,
    status TEXT DEFAULT 'Active', closed_date TEXT,
    down_payment REAL, other_charges REAL, installments_paid INTEGER, emi_paid REAL, emi_interest REAL,
    closing_payment REAL);
CREATE TABLE IF NOT EXISTS metals (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metal TEXT NOT NULL, form TEXT, description TEXT, date TEXT, grams REAL NOT NULL,
    rate_per_g REAL NOT NULL, making_charges REAL, gst REAL, other_charges REAL, purity TEXT, note TEXT);
CREATE TABLE IF NOT EXISTS real_estate (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL, kind TEXT, location TEXT, purchase_date TEXT, cost REAL NOT NULL,
    area_sqft REAL, registration REAL, brokerage REAL, other_charges REAL,
    rent_yielding INTEGER NOT NULL DEFAULT 0, monthly_rent REAL, current_per_sqft REAL, note TEXT);
CREATE TABLE IF NOT EXISTS target_pcts (bucket TEXT PRIMARY KEY, pct REAL NOT NULL CHECK (pct >= 0 AND pct <= 100));
CREATE TABLE IF NOT EXISTS notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    title TEXT NOT NULL, body TEXT, kind TEXT, done INTEGER NOT NULL DEFAULT 0, created TEXT, updated TEXT);
CREATE TABLE IF NOT EXISTS meta (key TEXT PRIMARY KEY, value TEXT);
"""

# table -> (persisted columns excluding id, columns holding dates, columns holding 0/1 flags)
TABLES: dict[str, tuple[list[str], set[str], set[str]]] = {
    "income": (["date", "source", "description", "amount"], {"date"}, set()),
    "securities": (["name", "symbol", "category", "reason", "current_price", "price_date", "currency"],
                   {"price_date"}, set()),
    "trades": (["security_id", "date", "side", "qty", "price", "fees", "note", "fx_rate"], {"date"}, set()),
    "dividends": (["security_id", "date", "amount", "note"], {"date"}, set()),
    "savings": (["kind", "name", "amount", "purpose", "interest_rate", "start_date", "maturity_date", "target", "note"],
                {"start_date", "maturity_date"}, set()),
    "insurance": (["name", "type", "provider", "policy_no", "sum_assured", "premium", "frequency", "next_due",
                   "link", "notes", "active", "start_date", "end_date", "insured_for"],
                  {"next_due", "start_date", "end_date"}, {"active"}),
    "insurance_revisions": (["insurance_id", "effective_from", "premium", "note"], {"effective_from"}, set()),
    "loans": (["name", "lender", "category", "principal", "outstanding", "interest_rate", "emi", "start_date",
               "end_date", "link", "details", "status", "closed_date", "down_payment", "other_charges",
               "installments_paid", "emi_paid", "emi_interest", "closing_payment"], {"start_date", "end_date", "closed_date"}, set()),
    "metals": (["metal", "form", "description", "date", "grams", "rate_per_g", "making_charges", "gst",
                "other_charges", "purity", "note"], {"date"}, set()),
    "real_estate": (["name", "kind", "location", "purchase_date", "cost", "area_sqft", "registration", "brokerage",
                     "other_charges", "rent_yielding", "monthly_rent", "current_per_sqft", "note"],
                    {"purchase_date"}, {"rent_yielding"}),
    "notes": (["title", "body", "kind", "done", "created", "updated"], set(), {"done"}),
}


@contextmanager
def connect(path: str | None = None):
    p = path or DB_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    conn = sqlite3.connect(p, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    try:
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


# columns added after the first release: (table, column, ddl)
_MIGRATIONS = [("securities", "currency", "TEXT DEFAULT 'INR'"), ("trades", "fx_rate", "REAL"),
               ("real_estate", "current_per_sqft", "REAL"),
               ("loans", "status", "TEXT DEFAULT 'Active'"), ("loans", "closed_date", "TEXT"), ("loans", "down_payment", "REAL"),
               ("loans", "other_charges", "REAL"), ("loans", "installments_paid", "INTEGER"),
               ("loans", "emi_paid", "REAL"), ("loans", "emi_interest", "REAL"), ("loans", "closing_payment", "REAL"),
               ("insurance", "start_date", "TEXT"), ("insurance", "end_date", "TEXT"),
               ("insurance", "insured_for", "TEXT")]


def init_db(path: str | None = None) -> None:
    with connect(path) as c:
        c.executescript(SCHEMA)
        for table, col, ddl in _MIGRATIONS:
            if col not in [r["name"] for r in c.execute(f"PRAGMA table_info({table})")]:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
        # real estate used to store a total current value; convert it to a per-sq-ft price
        if "current_value" in [r["name"] for r in c.execute("PRAGMA table_info(real_estate)")]:
            c.execute("UPDATE real_estate SET current_per_sqft = current_value / area_sqft "
                      "WHERE current_per_sqft IS NULL AND current_value > 0 AND area_sqft > 0")


# ---------- generic reads ----------
def get_table(table: str, path: str | None = None) -> pd.DataFrame:
    """Whole table as a DataFrame; date columns become datetime.date, flag columns become bool."""
    cols, dates, flags = TABLES[table]
    with connect(path) as c:
        df = pd.read_sql_query(f"SELECT id, {', '.join(cols)} FROM {table} ORDER BY id", c)
    for col in dates:
        df[col] = pd.to_datetime(df[col], errors="coerce").dt.date
    for col in flags:
        df[col] = df[col].fillna(0).astype(int).astype(bool)
    return df


# ---------- generic writes ----------
def _clean(value, is_date=False, is_flag=False):
    """DataFrame cell -> something sqlite understands (None for blanks)."""
    if value is None or (isinstance(value, float) and math.isnan(value)) or value is pd.NaT:
        return None
    try:
        if pd.isna(value):
            return None
    except (TypeError, ValueError):
        pass
    if is_flag:
        return 1 if bool(value) else 0
    if is_date:
        return pd.to_datetime(value).date().isoformat()
    if hasattr(value, "item"):  # numpy scalar
        value = value.item()
    if isinstance(value, str):
        value = value.strip()
        return value or None
    return value


def _row_values(row, table):
    cols, dates, flags = TABLES[table]
    return [_clean(row.get(col), col in dates, col in flags) for col in cols]


def sync_table(table: str, orig: pd.DataFrame, edited: pd.DataFrame, required=(), path: str | None = None) -> dict:
    """Persist what the user did in a data_editor: new rows -> INSERT, changed rows -> UPDATE, removed rows -> DELETE.

    `orig` is what the editor was given (has an `id` column); `edited` is what it returned. Rows missing a required
    value are skipped (counted in `skipped`) so half-typed rows never corrupt the table.
    """
    cols, _, _ = TABLES[table]
    stats = {"added": 0, "updated": 0, "deleted": 0, "skipped": 0}
    orig_by_id = {int(r["id"]): r for _, r in orig.iterrows()}
    seen: set[int] = set()
    with connect(path) as c:
        for _, row in edited.iterrows():
            vals = _row_values(row, table)
            by_col = dict(zip(cols, vals))
            rid = row.get("id")
            is_new = rid is None or pd.isna(rid)
            if all(v is None for k, v in by_col.items() if k not in _flag_cols(table)):
                continue  # fully blank row
            if any(by_col.get(r) is None for r in required):
                stats["skipped"] += 1
                if not is_new:
                    seen.add(int(rid))  # keep the old row instead of deleting it
                continue
            if is_new:
                c.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
                stats["added"] += 1
            else:
                rid = int(rid)
                seen.add(rid)
                if rid in orig_by_id and _row_values(orig_by_id[rid], table) != vals:
                    c.execute(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in cols)} WHERE id=?", vals + [rid])
                    stats["updated"] += 1
        for rid in orig_by_id:
            if rid not in seen:
                c.execute(f"DELETE FROM {table} WHERE id=?", (rid,))
                stats["deleted"] += 1
        if table == "insurance" and stats["deleted"]:   # a removed policy takes its premium changes with it
            c.execute("DELETE FROM insurance_revisions WHERE insurance_id NOT IN (SELECT id FROM insurance)")
        if table == "securities" and stats["deleted"]:  # a removed holding takes its history with it
            for child in ("trades", "dividends"):
                c.execute(f"DELETE FROM {child} WHERE security_id NOT IN (SELECT id FROM securities)")
    return stats


def _flag_cols(table):
    return TABLES[table][2]


def insert(table: str, record: dict, path: str | None = None) -> int:
    cols, dates, flags = TABLES[table]
    vals = [_clean(record.get(col), col in dates, col in flags) for col in cols]
    with connect(path) as c:
        cur = c.execute(f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join('?' * len(cols))})", vals)
        return cur.lastrowid


def update_row(table: str, row_id: int, record: dict, path: str | None = None) -> None:
    cols, dates, flags = TABLES[table]
    keys = [k for k in cols if k in record]
    with connect(path) as c:
        c.execute(f"UPDATE {table} SET {', '.join(f'{k}=?' for k in keys)} WHERE id=?",
                  [_clean(record[k], k in dates, k in flags) for k in keys] + [int(row_id)])


def delete_row(table: str, row_id: int, path: str | None = None) -> None:
    assert table in TABLES
    with connect(path) as c:
        c.execute(f"DELETE FROM {table} WHERE id=?", (int(row_id),))
        if table == "insurance":
            c.execute("DELETE FROM insurance_revisions WHERE insurance_id NOT IN (SELECT id FROM insurance)")


def delete_security(security_id: int, path: str | None = None) -> None:
    """Remove a holding together with its trades and dividends."""
    with connect(path) as c:
        c.execute("DELETE FROM trades WHERE security_id=?", (security_id,))
        c.execute("DELETE FROM dividends WHERE security_id=?", (security_id,))
        c.execute("DELETE FROM securities WHERE id=?", (security_id,))


# ---------- ideal allocation (percent of income) ----------
def get_target_pcts(path: str | None = None) -> dict[str, float]:
    with connect(path) as c:
        return {r["bucket"]: r["pct"] for r in c.execute("SELECT bucket, pct FROM target_pcts")}


def set_target_pcts(pcts: dict[str, float], path: str | None = None) -> None:
    with connect(path) as c:
        for bucket, pct in pcts.items():
            pct = min(max(float(pct or 0), 0.0), 100.0)
            if pct > 0:
                c.execute("INSERT INTO target_pcts(bucket, pct) VALUES (?,?) "
                          "ON CONFLICT(bucket) DO UPDATE SET pct=excluded.pct", (bucket, pct))
            else:
                c.execute("DELETE FROM target_pcts WHERE bucket=?", (bucket,))


# ---------- meta ----------
def meta_get(key: str, default=None, path: str | None = None):
    with connect(path) as c:
        r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def meta_set(key: str, value, path: str | None = None) -> None:
    with connect(path) as c:
        c.execute("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, str(value)))


def usd_inr(path: str | None = None) -> float:
    """USD->INR in force: the manual override when switched on, else the last live rate (0 if never known)."""
    if meta_get("fx_use_manual", "0", path) == "1":
        return float(meta_get("fx_manual", 0, path) or 0)
    return float(meta_get("fx_usdinr", 0, path) or 0)


def metal_premium(metal: str, path: str | None = None) -> float:
    v = meta_get(f"premium_{metal.lower()}", None, path)
    return float(v) if v not in (None, "") else DEFAULT_PREMIUM[metal]


def metal_prices(path: str | None = None) -> dict[str, float]:
    """Current price per gram, as typed by the user."""
    return {m: float(meta_get(f"price_{m.lower()}", 0, path) or 0) for m in METALS}


# ---------- backups ----------
def backup(path: str | None = None, keep: int = 30) -> str:
    """Consistent snapshot (SQLite backup API) into BACKUP_DIR/<db-stem>/<timestamp>.db; prunes old ones."""
    path = path or DB_PATH
    stem = os.path.splitext(os.path.basename(path))[0]
    dest_dir = os.path.join(BACKUP_DIR, stem)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3] + ".db")
    src, dst = sqlite3.connect(path), sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        src.close()
        dst.close()
    for old in sorted(os.listdir(dest_dir))[:-keep]:
        os.remove(os.path.join(dest_dir, old))
    return dest


def backup_if_stale(path: str | None = None, hours: int = 20) -> str | None:
    path = path or DB_PATH
    if not os.path.exists(path):
        return None
    d = os.path.join(BACKUP_DIR, os.path.splitext(os.path.basename(path))[0])
    if os.path.isdir(d) and os.listdir(d):
        newest = os.path.getmtime(os.path.join(d, sorted(os.listdir(d))[-1]))
        if datetime.now().timestamp() - newest < hours * 3600:
            return None
    return backup(path)


def restore(backup_file: str, path: str | None = None) -> None:
    path = path or DB_PATH
    backup(path)  # safety copy first
    shutil.copyfile(backup_file, path)


def today() -> date:
    return date.today()
