from datetime import date

import pandas as pd
import pytest

import analytics as an
import db


@pytest.fixture
def path(tmp_path):
    p = str(tmp_path / "t.db")
    db.init_db(p)
    return p


def _entries(path):
    return db.get_entries(path)


def test_monthly_summary(path):
    db.add_entry("2026-01-05", "inflow", "Salary", 1000, path=path)
    db.add_entry("2026-01-10", "fixed", "Rent", 400, path=path)
    db.add_entry("2026-01-12", "variable", "Dining", 100, path=path)
    db.add_entry("2026-03-01", "inflow", "Salary", 1000, path=path)
    ms = an.monthly_summary(_entries(path))
    assert len(ms) == 3  # Feb filled with zeros
    jan = ms.iloc[0]
    assert jan["net"] == 500 and jan["savings_rate"] == 0.5 and jan["fixed_ratio"] == 0.4


def test_spike_alert(path):
    for m in range(1, 7):
        db.add_entry(f"2026-{m:02d}-03", "variable", "Dining", 1000, path=path)
        db.add_entry(f"2026-{m:02d}-01", "inflow", "Salary", 50000, path=path)
    db.add_entry("2026-07-04", "variable", "Dining", 5000, path=path)
    alerts = an.detect_alerts(_entries(path), today=date(2026, 7, 10))
    assert any("Dining" in a.title for a in alerts)


def test_no_alert_when_normal(path):
    for m in range(1, 8):
        db.add_entry(f"2026-{m:02d}-03", "variable", "Dining", 1000, path=path)
        db.add_entry(f"2026-{m:02d}-01", "inflow", "Salary", 50000, path=path)
    assert not [a for a in an.detect_alerts(_entries(path), today=date(2026, 7, 10)) if "Dining" in a.title]


def test_negative_cashflow_alert(path):
    for m in range(1, 4):
        db.add_entry(f"2026-{m:02d}-01", "inflow", "Salary", 1000, path=path)
    db.add_entry("2026-04-01", "inflow", "Salary", 1000, path=path)
    db.add_entry("2026-04-02", "variable", "Shopping", 3000, path=path)
    alerts = an.detect_alerts(_entries(path), today=date(2026, 4, 5))
    assert any(a.title == "Negative cash flow" for a in alerts)


def test_upcoming_and_autopost(path):
    db.add_fixed_item("Gym", "Gym", 1500, "monthly", "2026-11-14", path=path)
    up = an.upcoming_fixed(db.get_fixed_items(path), today=date(2026, 11, 9), days=30)
    assert list(up["in_days"]) == [5]
    n = db.post_due_fixed(today=date(2027, 1, 20), path=path)  # Nov 14, Dec 14, Jan 14
    assert n == 3
    assert db.get_fixed_items(path).iloc[0]["next_due"] == "2027-02-14"
    assert db.post_due_fixed(today=date(2027, 1, 20), path=path) == 0  # idempotent


def test_month_end_advance():
    assert db._advance(date(2026, 1, 31), "monthly") == date(2026, 2, 28)


def test_forecast_and_yoy(path):
    for m in range(1, 7):
        db.add_entry(f"2026-{m:02d}-01", "inflow", "Salary", 1000, path=path)
        db.add_entry(f"2026-{m:02d}-02", "variable", "Food", 200, path=path)
    fc = an.forecast(_entries(path), db.get_fixed_items(path), months=3, today=date(2026, 7, 1))
    assert len(fc) == 3 and fc["inflow"].iloc[0] == 1000
    yoy = an.yearly_comparison(an.monthly_summary(_entries(path)))
    assert yoy.shape == (12, 1)


def test_users_pin_and_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(db, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(db, "BACKUP_DIR", str(tmp_path / "bk"))
    p = db.create_user("Asha", "1234")
    assert p == db.user_path("asha")  # name hashed case-insensitively
    assert not db.verify_pin(p, "0000") and db.verify_pin(p, "1234")
    with pytest.raises(ValueError):
        db.create_user("ASHA")
    q = db.create_user("Ravi")
    assert db.verify_pin(q, "") and {u["name"] for u in db.list_users()} == {"Asha", "Ravi"}
    db.add_entry("2026-01-01", "inflow", "Salary", 10, path=q)
    snap = db.backup(q)
    db.delete_entries(db.get_entries(q)["id"].tolist(), path=q)
    assert db.get_entries(q).empty
    db.restore(snap, q)
    assert len(db.get_entries(q)) == 1


def test_active_user_is_isolated_per_thread(tmp_path):
    """Two concurrent sessions (threads) must never read/write each other's database."""
    import threading

    pa, pb = str(tmp_path / "a.db"), str(tmp_path / "b.db")
    for p in (pa, pb):
        db.init_db(p)
    barrier, errors = threading.Barrier(2), []

    def session(path, label):
        try:
            db.set_active(path)
            barrier.wait()  # both threads have selected their user at the same moment
            for i in range(20):
                db.add_entry("2026-01-01", "variable", label, 1)
                assert set(db.get_entries()["category"]) == {label}
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    ts = [threading.Thread(target=session, args=(pa, "A")), threading.Thread(target=session, args=(pb, "B"))]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert not errors, errors
    assert len(db.get_entries(pa)) == 20 and len(db.get_entries(pb)) == 20


def test_letter_case_and_tags(path):
    assert db.fmt("  home LOAN   emi ") == "Home Loan EMI"
    assert db.fmt("EMI") == "EMI" and db.fmt("sip") == "SIP" and db.fmt("GYM") == "Gym"
    assert db.split_tags("trip, TRIP ,medical,, ") == ["Trip", "Medical"]
    db.add_entry("2026-01-01", "fixed", "EMI", 100, path=path)
    db.add_entry("2026-01-02", "variable", "dining out", 50, tag=["impromptu", "Late Night"], path=path)
    db.add_entry("2026-01-03", "variable", "DINING OUT", 60, tag="IMPROMPTU", path=path)  # merges, not a new category
    db.add_entry("2026-01-04", "fixed", "emi", 100, path=path)  # reuses the stored 'EMI'
    df = db.get_entries(path)
    assert set(df["category"]) == {"EMI", "Dining Out"}
    assert set(df["tag"]) == {"", "Impromptu, Late Night", "Impromptu"}
    assert db.all_tags(path) == ["Impromptu", "Late Night"]


def test_normalize_existing_merges_case_variants(path):
    with db.connect(path) as c:
        c.execute("INSERT INTO entries(date,type,category,amount,tag) VALUES ('2026-01-01','variable','dining',1,'habit')")
        c.execute("INSERT INTO entries(date,type,category,amount,tag) VALUES ('2026-01-02','variable','DINING',1,'Habit, trip')")
    db.init_db(path)
    df = db.get_entries(path)
    assert set(df["category"]) == {"Dining"} and set(df["tag"]) == {"Habit", "Habit, Trip"}


def test_monthly_day_does_not_drift(path):
    db.add_fixed_item("Rent", "Rent", 100, "monthly", "2026-01-31", path=path)
    db.post_due_fixed(today=date(2026, 5, 31), path=path)
    days = sorted(db.get_entries(path)["date"].dt.strftime("%m-%d"))
    assert days == ["01-31", "02-28", "03-31", "04-30", "05-31"]


def test_quarterly_and_yearly_are_calendar_based(path):
    assert db._advance(date(2026, 1, 15), "quarterly") == date(2026, 4, 15)
    assert db._advance(date(2026, 11, 30), "quarterly") == date(2027, 2, 28)
    assert db._advance(date(2024, 2, 29), "yearly") == date(2025, 2, 28)
    assert db._advance(date(2026, 3, 1), "weekly") == date(2026, 3, 8)


def test_end_date_stops_posting_and_retires_item(path):
    db.add_fixed_item("Car EMI", "EMI", 500, "monthly", "2026-01-10", end_date="2026-03-20", method="debit card", path=path)
    assert db.post_due_fixed(today=date(2026, 12, 1), path=path) == 3  # Jan, Feb, Mar only
    df = db.get_entries(path)
    assert set(df["method"]) == {"Debit Card"}
    item = db.get_fixed_items(path).iloc[0]
    assert item["active"] == 0
    assert an.upcoming_fixed(db.get_fixed_items(path).assign(active=1), today=date(2026, 12, 1), days=90).empty


def test_end_before_start_rejected(path):
    with pytest.raises(ValueError):
        db.add_fixed_item("X", "X", 1, "monthly", "2026-05-01", end_date="2026-04-01", path=path)


def test_payment_method_only_on_expenses(path):
    db.add_entry("2026-01-01", "inflow", "Salary", 10, method="upi", path=path)
    db.add_entry("2026-01-02", "variable", "Dining", 5, method="credit card", path=path)
    df = db.get_entries(path).set_index("category")
    assert df.loc["Salary", "method"] == "" and df.loc["Dining", "method"] == "Credit Card"


def test_migrates_old_database(tmp_path):
    import sqlite3
    p = str(tmp_path / "old.db")
    c = sqlite3.connect(p)
    c.executescript("""CREATE TABLE entries (id INTEGER PRIMARY KEY AUTOINCREMENT, date TEXT NOT NULL, type TEXT NOT NULL,
        category TEXT NOT NULL, amount REAL NOT NULL, note TEXT DEFAULT '', tag TEXT DEFAULT '', fixed_item_id INTEGER);
        CREATE TABLE fixed_items (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL, category TEXT NOT NULL,
        amount REAL NOT NULL, frequency TEXT NOT NULL, next_due TEXT NOT NULL, active INTEGER NOT NULL DEFAULT 1);
        INSERT INTO fixed_items(name,category,amount,frequency,next_due) VALUES ('Gym','Gym',1,'monthly','2026-01-05');""")
    c.commit(); c.close()
    db.init_db(p)
    assert db.post_due_fixed(today=date(2026, 3, 10), path=p) == 3
    assert list(db.get_entries(p)["method"]) == ["", "", ""]


def test_concurrent_sessions_do_not_double_post(path):
    import threading
    db.add_fixed_item("Gym", "Gym", 1, "monthly", "2026-01-05", path=path)
    barrier = threading.Barrier(4)

    def go():
        barrier.wait()
        db.post_due_fixed(today=date(2026, 3, 10), path=path)

    ts = [threading.Thread(target=go) for _ in range(4)]
    [t.start() for t in ts]; [t.join() for t in ts]
    assert len(db.get_entries(path)) == 3  # Jan, Feb, Mar - once each
