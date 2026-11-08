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
    fixed_item_id INTEGER
);
CREATE TABLE IF NOT EXISTS fixed_items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    category TEXT NOT NULL,
    amount REAL NOT NULL CHECK (amount >= 0),
    frequency TEXT NOT NULL,
    next_due TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1
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


def init_db(path: str | None = None) -> None:
    with connect(path) as c:
        c.executescript(SCHEMA)


# ---------- entries ----------
def add_entry(date_, type_, category, amount, note="", tag="", fixed_item_id=None, path=None):
    if type_ not in ENTRY_TYPES:
        raise ValueError(f"type must be one of {ENTRY_TYPES}")
    with connect(path) as c:
        c.execute(
            "INSERT INTO entries(date,type,category,amount,note,tag,fixed_item_id) VALUES (?,?,?,?,?,?,?)",
            (str(date_), type_, category.strip(), float(amount), note, tag, fixed_item_id),
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
def add_fixed_item(name, category, amount, frequency, next_due, path=None):
    if frequency not in FREQUENCIES:
        raise ValueError(f"frequency must be one of {list(FREQUENCIES)}")
    with connect(path) as c:
        c.execute(
            "INSERT INTO fixed_items(name,category,amount,frequency,next_due) VALUES (?,?,?,?,?)",
            (name.strip(), category.strip(), float(amount), frequency, str(next_due)),
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


def _advance(due: date, frequency: str) -> date:
    if frequency == "monthly":
        m = due.month % 12 + 1
        y = due.year + (due.month == 12)
        day = min(due.day, [31, 29 if y % 4 == 0 and (y % 100 or y % 400 == 0) else 28,
                            31, 30, 31, 30, 31, 31, 30, 31, 30, 31][m - 1])
        return date(y, m, day)
    if frequency == "yearly":
        try:
            return due.replace(year=due.year + 1)
        except ValueError:  # Feb 29
            return due.replace(year=due.year + 1, day=28)
    return due + timedelta(days=FREQUENCIES[frequency])


def post_due_fixed(today: date | None = None, path=None) -> int:
    """Auto-post every active fixed item whose due date has passed. Returns count posted."""
    today = today or date.today()
    posted = 0
    items = get_fixed_items(path)
    for _, it in items[items["active"] == 1].iterrows():
        due = date.fromisoformat(it["next_due"])
        while due <= today:
            add_entry(due, "fixed", it["category"], it["amount"], note=it["name"],
                      fixed_item_id=int(it["id"]), path=path)
            posted += 1
            due = _advance(due, it["frequency"])
        with connect(path) as c:
            c.execute("UPDATE fixed_items SET next_due=? WHERE id=?", (str(due), int(it["id"])))
    return posted


# ---------- budgets ----------
def set_budget(category, amount, path=None):
    with connect(path) as c:
        c.execute(
            "INSERT INTO budgets(category,amount) VALUES (?,?) "
            "ON CONFLICT(category) DO UPDATE SET amount=excluded.amount",
            (category.strip(), float(amount)),
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
