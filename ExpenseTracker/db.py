"""SQLite data layer for the Expense Tracker."""
from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import date, timedelta

import pandas as pd

import hashlib
import secrets
import shutil
import threading
from datetime import datetime

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("EXPENSE_DATA_DIR", os.path.join(BASE_DIR, "data"))
BACKUP_DIR = os.environ.get("EXPENSE_BACKUP_DIR", os.path.join(BASE_DIR, "backups"))
# The current user's DB file. Thread-local, NOT global: Streamlit runs every browser session in its own
# thread, and app.py calls set_active() at the start of each run, so sessions can never see each other's DB.
_local = threading.local()


def get_active() -> str | None:
    return getattr(_local, "path", None)

ENTRY_TYPES = ("inflow", "fixed", "variable")
FREQUENCIES = {"weekly": 7, "biweekly": 14, "monthly": 30, "quarterly": 91, "yearly": 365}

SCHEMA = """
CREATE TABLE IF NOT EXISTS entries (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    date TEXT NOT NULL,
    type TEXT NOT NULL CHECK (type IN ('inflow','fixed','variable')),
    category TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0),
    note TEXT DEFAULT '',
    tag TEXT DEFAULT '',
    fixed_item_id INTEGER,
    method TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS fixed_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0),
    frequency TEXT NOT NULL,
    next_due TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    end_date TEXT,
    anchor_day INTEGER,
    method TEXT DEFAULT ''
);
CREATE TABLE IF NOT EXISTS meta (
    key TEXT PRIMARY KEY,
    value TEXT
);
CREATE TABLE IF NOT EXISTS budgets (
    category TEXT PRIMARY KEY,
    amount REAL NOT NULL CHECK (amount >= 0)
);
"""


@contextmanager
def connect(path: str | None = None):
    p = path or get_active()
    if p is None:
        raise RuntimeError("No active user: call db.set_active(path) first")
    conn = sqlite3.connect(p, timeout=15)  # wait for another session's write instead of failing
    conn.row_factory = sqlite3.Row
    try:
        yield conn
        conn.commit()
    finally:
        conn.close()


# ---------- letter case ----------
ACRONYMS = {"EMI", "SIP", "UPI", "ATM", "GST", "PPF", "EPF", "PF", "NPS", "FD", "RD", "ETF", "HRA", "LIC",
            "TV", "AC", "ID", "SMS", "OTT", "IT"}


def fmt(text) -> str:
    """Letter Case for labels: 'home LOAN  emi' -> 'Home Loan EMI'. Known finance acronyms stay upper-case."""
    out = []
    for w in " ".join(str(text or "").split()).split(" "):
        out.append(w.upper() if w.upper() in ACRONYMS else w[:1].upper() + w[1:].lower())
    return " ".join(out)


def split_tags(tags) -> list[str]:
    """'Trip, Medical' / ['Trip','Medical'] -> ['Trip','Medical'] (no blanks, no case-duplicates)."""
    parts = tags.split(",") if isinstance(tags, str) else list(tags or [])
    seen, out = set(), []
    for t in parts:
        t = fmt(t)
        if t and t.casefold() not in seen:
            seen.add(t.casefold())
            out.append(t)
    return out


def _canonical(value: str, existing) -> str:
    """Format `value`, but reuse the spelling already stored if it matches ignoring case (e.g. keep 'EMI')."""
    v = fmt(value)
    for e in existing:
        if e and e.casefold() == v.casefold():
            return e
    return v


def _distinct(column: str, table="entries", path=None) -> list[str]:
    with connect(path) as c:
        return [r[0] for r in c.execute(f"SELECT DISTINCT {column} FROM {table}") if r[0]]


def all_tags(path=None) -> list[str]:
    seen: dict[str, str] = {}
    for row in _distinct("tag", path=path):
        for t in split_tags(row):
            seen.setdefault(t.casefold(), t)
    return sorted(seen.values())


def normalize_existing(path=None) -> None:
    """Idempotent clean-up: Letter Case + merge case-variants for categories, tags and fixed-item names."""
    with connect(path) as c:
        canon: dict[str, str] = {}

        def cn(v):
            f = fmt(v)
            return canon.setdefault(f.casefold(), f) if f else f

        for r in c.execute("SELECT id, category, tag FROM entries").fetchall():
            cat, tag = cn(r["category"]), ", ".join(cn(t) for t in split_tags(r["tag"]))
            if (cat, tag) != (r["category"], r["tag"]):
                c.execute("UPDATE entries SET category=?, tag=? WHERE id=?", (cat, tag, r["id"]))
        for r in c.execute("SELECT id, name, category FROM fixed_items").fetchall():
            name, cat = fmt(r["name"]), cn(r["category"])
            if (name, cat) != (r["name"], r["category"]):
                c.execute("UPDATE fixed_items SET name=?, category=? WHERE id=?", (name, cat, r["id"]))
        for r in c.execute("SELECT category FROM budgets").fetchall():
            cat = cn(r["category"])
            if cat != r["category"] and not c.execute("SELECT 1 FROM budgets WHERE category=?", (cat,)).fetchone():
                c.execute("UPDATE budgets SET category=? WHERE category=?", (cat, r["category"]))


_MIGRATIONS = {
    "entries": {"method": "TEXT DEFAULT ''"},
    "fixed_items": {"end_date": "TEXT", "anchor_day": "INTEGER", "method": "TEXT DEFAULT ''"},
}


def _migrate(c) -> None:
    for table, cols in _MIGRATIONS.items():
        have = {r["name"] for r in c.execute(f"PRAGMA table_info({table})")}
        for col, ddl in cols.items():
            if col not in have:
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")


def init_db(path: str | None = None) -> None:
    with connect(path) as c:
        c.executescript(SCHEMA)
        _migrate(c)
    normalize_existing(path)


# ---------- entries ----------
def add_entry(date_, type_, category, amount, note="", tag="", fixed_item_id=None, method="", path=None):
    if type_ not in ENTRY_TYPES:
        raise ValueError(f"type must be one of {ENTRY_TYPES}")
    category = _canonical(category, _distinct("category", path=path))
    if not category:
        raise ValueError("Category required")
    known = {t.casefold(): t for t in all_tags(path)}
    tag = ", ".join(known.get(t.casefold(), t) for t in split_tags(tag))
    with connect(path) as c:
        c.execute(
            "INSERT INTO entries(date,type,category,amount,note,tag,fixed_item_id,method) VALUES (?,?,?,?,?,?,?,?)",
            (str(date_), type_, category, float(amount), note.strip(), tag, fixed_item_id,
             "" if type_ == "inflow" else fmt(method)),
        )


def delete_entry(entry_id, path=None):
    with connect(path) as c:
        c.execute("DELETE FROM entries WHERE id=?", (entry_id,))


def get_entries(path=None) -> pd.DataFrame:
    with connect(path) as c:
        df = pd.read_sql_query("SELECT * FROM entries ORDER BY date DESC, id DESC", c)
    df["date"] = pd.to_datetime(df["date"])
    return df


# ---------- fixed items ----------
def add_fixed_item(name, category, amount, frequency, next_due, end_date=None, method="", path=None):
    """`next_due` is the first charge date; `end_date` (optional) is the last day a charge may fall on."""
    if frequency not in FREQUENCIES:
        raise ValueError(f"frequency must be one of {list(FREQUENCIES)}")
    start = date.fromisoformat(str(next_due))
    if end_date and date.fromisoformat(str(end_date)) < start:
        raise ValueError("End date is before the start date")
    cat = _canonical(category, _distinct("category", path=path) + _distinct("category", "fixed_items", path))
    with connect(path) as c:
        c.execute(
            "INSERT INTO fixed_items(name,category,amount,frequency,next_due,end_date,anchor_day,method)"
            " VALUES (?,?,?,?,?,?,?,?)",
            (fmt(name), cat, float(amount), frequency, str(start), str(end_date) if end_date else None,
             start.day, fmt(method)),
        )


def get_fixed_items(path=None) -> pd.DataFrame:
    with connect(path) as c:
        return pd.read_sql_query("SELECT * FROM fixed_items ORDER BY next_due", c)


def set_fixed_active(item_id, active, path=None):
    with connect(path) as c:
        c.execute("UPDATE fixed_items SET active=? WHERE id=?", (int(active), item_id))


def delete_fixed_item(item_id, path=None):
    with connect(path) as c:
        c.execute("DELETE FROM fixed_items WHERE id=?", (item_id,))


_MONTH_STEP = {"monthly": 1, "quarterly": 3, "yearly": 12}


def _add_months(d: date, months: int, anchor_day: int | None = None) -> date:
    """Calendar-aware month arithmetic that remembers the intended day (31st -> Feb 28 -> Mar 31)."""
    y, m = divmod(d.year * 12 + d.month - 1 + months, 12)
    m += 1
    last = (date(y + (m == 12), m % 12 + 1, 1) - timedelta(days=1)).day
    return date(y, m, min(anchor_day or d.day, last))


def _advance(due: date, frequency: str, anchor_day: int | None = None) -> date:
    if frequency in _MONTH_STEP:
        return _add_months(due, _MONTH_STEP[frequency], anchor_day)
    return due + timedelta(days=FREQUENCIES[frequency])


def _anchor(it) -> int | None:
    a = it.get("anchor_day") if hasattr(it, "get") else None
    return None if a is None or a != a else int(a)  # NaN-safe


def _end(it) -> date | None:
    e = it.get("end_date") if hasattr(it, "get") else None
    return date.fromisoformat(e) if isinstance(e, str) and e else None


def post_due_fixed(today: date | None = None, path=None) -> int:
    """Post every charge that has come due (also those missed while the app was closed).

    Runs in one write transaction (BEGIN IMMEDIATE) so two devices opening the app at the same
    moment cannot post the same charge twice. Items past their end date are retired.
    """
    today = today or date.today()
    p = path or get_active()
    if p is None:
        raise RuntimeError("No active user: call db.set_active(path) first")
    conn = sqlite3.connect(p, timeout=15, isolation_level=None)
    conn.row_factory = sqlite3.Row
    posted = 0
    try:
        conn.execute("BEGIN IMMEDIATE")
        for it in conn.execute("SELECT * FROM fixed_items WHERE active=1").fetchall():
            it = dict(it)
            due, end, anchor = date.fromisoformat(it["next_due"]), _end(it), _anchor(it)
            while due <= today and (end is None or due <= end):
                conn.execute(
                    "INSERT INTO entries(date,type,category,amount,note,tag,fixed_item_id,method) "
                    "VALUES (?,?,?,?,?,?,?,?)",
                    (str(due), "fixed", it["category"], it["amount"], it["name"], "", it["id"],
                     it.get("method") or ""))
                posted += 1
                due = _advance(due, it["frequency"], anchor)
            finished = end is not None and due > end
            conn.execute("UPDATE fixed_items SET next_due=?, active=? WHERE id=?",
                         (str(due), 0 if finished else 1, it["id"]))
        conn.execute("COMMIT")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    finally:
        conn.close()
    return posted


# ---------- budgets ----------
def set_budget(category, amount, path=None):
    with connect(path) as c:
        c.execute(
            "INSERT INTO budgets(category,amount) VALUES (?,?) "
            "ON CONFLICT(category) DO UPDATE SET amount=excluded.amount",
            (_canonical(category, _distinct("category", path=path) + _distinct("category", "budgets", path)),
             float(amount)),
        )


def get_budgets(path=None) -> pd.DataFrame:
    with connect(path) as c:
        return pd.read_sql_query("SELECT * FROM budgets ORDER BY category", c)


def delete_budget(category, path=None):
    with connect(path) as c:
        c.execute("DELETE FROM budgets WHERE category=?", (category,))


def delete_entries(ids, path=None):
    ids = [int(i) for i in ids]
    if not ids:
        return 0
    with connect(path) as c:
        c.execute(f"DELETE FROM entries WHERE id IN ({','.join('?' * len(ids))})", ids)
    return len(ids)


# ---------- users (one SQLite file per user, named by a SHA-256 of the name) ----------
def set_active(path: str | None) -> None:
    _local.path = path


def user_path(name: str) -> str:
    h = hashlib.sha256(name.strip().lower().encode()).hexdigest()[:12]
    return os.path.join(DATA_DIR, f"{h}.db")


def _hash_pin(pin: str, salt: str) -> str:
    return hashlib.pbkdf2_hmac("sha256", pin.encode(), bytes.fromhex(salt), 100_000).hex()


def _meta_set(path, key, value):
    with connect(path) as c:
        c.execute("INSERT INTO meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value",
                  (key, value))


def _meta_get(path, key, default=None):
    with connect(path) as c:
        r = c.execute("SELECT value FROM meta WHERE key=?", (key,)).fetchone()
    return r["value"] if r else default


def create_user(name: str, pin: str = "") -> str:
    name = name.strip()
    if not name:
        raise ValueError("Name required")
    os.makedirs(DATA_DIR, exist_ok=True)
    path = user_path(name)
    if os.path.exists(path):
        raise ValueError(f"User '{name}' already exists")
    init_db(path)
    _meta_set(path, "name", name)
    if pin:
        salt = secrets.token_hex(16)
        _meta_set(path, "pin_salt", salt)
        _meta_set(path, "pin_hash", _hash_pin(pin, salt))
    return path


def list_users() -> list[dict]:
    out = []
    if os.path.isdir(DATA_DIR):
        for f in sorted(os.listdir(DATA_DIR)):
            if f.endswith(".db"):
                p = os.path.join(DATA_DIR, f)
                try:
                    out.append({"name": _meta_get(p, "name", f[:-3]), "path": p,
                                "locked": _meta_get(p, "pin_hash") is not None})
                except sqlite3.Error:
                    pass
    return out


def verify_pin(path: str, pin: str) -> bool:
    h = _meta_get(path, "pin_hash")
    if h is None:
        return True
    return secrets.compare_digest(h, _hash_pin(pin, _meta_get(path, "pin_salt")))


def delete_user(path: str) -> None:
    if os.path.exists(path):
        os.remove(path)


# ---------- backups ----------
def backup(path: str | None = None, keep: int = 30) -> str:
    """Consistent snapshot (SQLite backup API) into BACKUP_DIR/<db-stem>/<timestamp>.db; prunes old ones."""
    path = path or get_active()
    stem = os.path.splitext(os.path.basename(path))[0]
    dest_dir = os.path.join(BACKUP_DIR, stem)
    os.makedirs(dest_dir, exist_ok=True)
    dest = os.path.join(dest_dir, datetime.now().strftime("%Y%m%d-%H%M%S-%f")[:-3] + ".db")  # ms precision: no same-second clobbering
    src, dst = sqlite3.connect(path), sqlite3.connect(dest)
    try:
        src.backup(dst)
    finally:
        src.close(); dst.close()
    for old in sorted(os.listdir(dest_dir))[:-keep]:
        os.remove(os.path.join(dest_dir, old))
    return dest


def backup_if_stale(path: str, hours: int = 20) -> str | None:
    d = os.path.join(BACKUP_DIR, os.path.splitext(os.path.basename(path))[0])
    if os.path.isdir(d) and os.listdir(d):
        newest = os.path.getmtime(os.path.join(d, sorted(os.listdir(d))[-1]))
        if datetime.now().timestamp() - newest < hours * 3600:
            return None
    return backup(path)


def restore(backup_file: str, path: str) -> None:
    backup(path)  # safety copy first
    shutil.copyfile(backup_file, path)
