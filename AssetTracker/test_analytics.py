import os
import tempfile
from datetime import date

import pandas as pd
import pytest

import analytics as an
import db


@pytest.fixture()
def path():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.db")
        db.init_db(p)
        yield p


def frames(securities, trades, dividends=()):
    s = pd.DataFrame(securities, columns=["id", "name", "symbol", "category", "reason", "current_price"])
    t = pd.DataFrame(trades, columns=["id", "security_id", "date", "side", "qty", "price", "fees"])
    d = pd.DataFrame(dividends, columns=["id", "security_id", "date", "amount"])
    return s, t, d


def test_inr_grouping():
    assert an.inr(0) == "₹0"
    assert an.inr(999) == "₹999"
    assert an.inr(1234567) == "₹12,34,567"
    assert an.inr(-100000) == "-₹1,00,000"
    assert an.inr(None) == "–"


def test_average_cost_and_pnl():
    s, t, d = frames([(1, "ABC", "", "India - High Quality", "moat", 150.0)],
                     [(1, 1, "2025-01-01", "Buy", 10, 100, 0), (2, 1, "2025-02-01", "Buy", 10, 120, 20)])
    pos, realised, w = an.build_positions(s, t, d)
    r = pos.iloc[0]
    assert r.qty == 20 and r.invested == 2220 and r.avg_price == pytest.approx(111)
    assert r.value == 3000 and r.pnl == 780 and r.first_buy == "2025-01-01" and realised.empty and not w


def test_sell_realises_at_average_cost_and_keeps_avg():
    s, t, d = frames([(1, "ABC", "", "ETFs", "", 100.0)],
                     [(1, 1, "2025-01-01", "Buy", 10, 100, 0), (2, 1, "2025-02-01", "Buy", 10, 200, 0),
                      (3, 1, "2025-03-01", "Sell", 5, 250, 10)])
    pos, realised, _ = an.build_positions(s, t, d)
    assert realised.iloc[0].pnl == pytest.approx(5 * 250 - 10 - 5 * 150)
    r = pos.iloc[0]
    assert r.qty == 15 and r.avg_price == pytest.approx(150) and r.realised == pytest.approx(490)


def test_oversell_is_capped_and_warned():
    s, t, d = frames([(1, "ABC", "", "ETFs", "", 10.0)],
                     [(1, 1, "2025-01-01", "Buy", 5, 10, 0), (2, 1, "2025-02-01", "Sell", 8, 12, 0)])
    pos, _, w = an.build_positions(s, t, d)
    assert pos.iloc[0].qty == 0 and len(w) == 1


def test_dividends_weights_and_missing_price():
    s, t, d = frames([(1, "A", "", "ETFs", "", None), (2, "B", "", "ETFs", "", 30.0)],
                     [(1, 1, "2025-01-01", "Buy", 10, 10, 0), (2, 2, "2025-01-01", "Buy", 10, 20, 0)],
                     [(1, 2, "2025-05-01", 50), (2, 2, "2025-08-01", 60)])
    pos, _, _ = an.build_positions(s, t, d)
    a, b = pos.iloc[0], pos.iloc[1]
    assert a.value == 100  # no price typed -> valued at cost
    assert b.value == 300 and b.dividends == 110 and b.last_dividend == "2025-08-01" and b.dividend_count == 2
    assert a.weight_pct + b.weight_pct == pytest.approx(100)


def test_income_pivot_includes_auto_rows():
    inc = pd.DataFrame({"date": [date(2025, 1, 31), date(2025, 2, 28)], "source": ["Salary", "Salary"],
                        "amount": [100.0, 100.0]})
    div = pd.DataFrame({"date": ["2025-02-10"], "amount": [5.0]})
    rl = pd.DataFrame({"date": ["2025-02-11"], "security_id": [1], "pnl": [-20.0]})
    df = an.income_frame(inc, div, rl, include_auto=True)
    p = an.income_pivot(df, 2025)
    assert p.loc["Salary", 2] == 100 and p.loc["Dividend/FD", 2] == 5 and p.loc["PL Booking", 2] == -20
    assert an.income_frame(inc, div, rl, include_auto=False)["amount"].sum() == 200
    assert an.income_pivot(df, None, by="year").loc["Salary", 2025] == 200


def test_insurance_annualised_and_lapsed():
    ins = pd.DataFrame({"premium": [1000, 12000, 500], "frequency": ["Monthly", "Yearly", "Quarterly"],
                        "active": [True, True, False]})
    f = an.insurance_frame(ins)
    assert list(f.per_year) == [12000, 12000, 0] and list(f.per_month) == [1000, 1000, 0]


def test_metals_charges_and_value():
    m = pd.DataFrame({"metal": ["Gold"], "grams": [10], "rate_per_g": [6000], "making_charges": [2000],
                      "gst": [1860], "other_charges": [None]})
    f = an.metals_frame(m, {"Gold": 7000.0}).iloc[0]
    assert f.invested == 63860 and f.value == 70000 and f.charges == 3860 and f.pnl == 6140


def test_real_estate_metrics():
    r = pd.DataFrame({"cost": [5_000_000], "area_sqft": [1000], "registration": [300_000], "brokerage": [50_000],
                      "other_charges": [None], "rent_yielding": [True], "monthly_rent": [20_000],
                      "current_value": [None]})
    f = an.real_estate_frame(r).iloc[0]
    assert f.cost_per_sqft == 5000 and f.total_cost == 5_350_000 and f.value == 5_000_000
    assert f.rent_yield_pct == pytest.approx(240_000 / 5_350_000 * 100)


def test_allocation_target_vs_reality():
    b = pd.DataFrame({"bucket": ["Gold", "Crypto"], "value": [300.0, 100.0], "invested": [0, 0]})
    a = an.allocation(b, {"Gold": 200, "Crypto": 200}).set_index("bucket")
    assert a.loc["Gold", "actual_pct"] == 75 and a.loc["Gold", "target_pct"] == 50
    assert a.loc["Gold", "gap"] == -100 and a.loc["Crypto", "gap"] == 100


def test_sync_table_add_update_delete(path):
    db.insert("income", {"date": date(2025, 1, 1), "source": "Salary", "amount": 10}, path)
    db.insert("income", {"date": date(2025, 1, 2), "source": "Salary", "amount": 20}, path)
    orig = db.get_table("income", path)
    ed = orig.copy()
    ed = ed.iloc[1:].reset_index(drop=True)            # delete row 1
    ed.loc[0, "amount"] = 25.0                          # update row 2
    ed.loc[len(ed)] = [None, date(2025, 2, 1), "Misc", "gift", 5.0]       # add
    ed.loc[len(ed)] = [None, None, None, None, None]                      # blank: ignored
    ed.loc[len(ed)] = [None, date(2025, 3, 1), "Salary", None, None]      # no amount: skipped
    stats = db.sync_table("income", orig, ed, required=("date", "source", "amount"), path=path)
    assert stats == {"added": 1, "updated": 1, "deleted": 1, "skipped": 1}
    now = db.get_table("income", path)
    assert list(now.amount) == [25.0, 5.0] and now.iloc[1].date == date(2025, 2, 1)


def test_sync_keeps_row_when_required_value_cleared(path):
    db.insert("income", {"date": date(2025, 1, 1), "source": "Salary", "amount": 10}, path)
    orig = db.get_table("income", path)
    ed = orig.copy()
    ed.loc[0, "amount"] = None
    stats = db.sync_table("income", orig, ed, required=("amount",), path=path)
    assert stats["deleted"] == 0 and len(db.get_table("income", path)) == 1


def test_flags_roundtrip_and_targets(path):
    db.insert("insurance", {"name": "Term", "premium": 1, "frequency": "Yearly", "active": True}, path)
    assert db.get_table("insurance", path)["active"].iloc[0] in (True, False)
    db.set_targets({"Gold": 100, "Crypto": 0}, path)
    assert db.get_targets(path) == {"Gold": 100.0}


def test_loans_markdown_and_security_cascade(path):
    l = pd.DataFrame({"name": ["Car"], "lender": ["Bank"], "category": ["Bad"], "principal": [100.0],
                      "outstanding": [40.0], "interest_rate": [9.5], "emi": [5.0], "start_date": [None],
                      "end_date": [None], "link": ["http://x"], "details": ["Paid from salary"]})
    md = an.loans_markdown(l)
    assert "Car" in md and "60% repaid" in md and "http://x" in md and "Paid from salary" in md

    sid = db.insert("securities", {"name": "A", "category": "ETFs"}, path)
    db.insert("trades", {"security_id": sid, "date": date(2025, 1, 1), "side": "Buy", "qty": 1, "price": 1}, path)
    db.insert("dividends", {"security_id": sid, "date": date(2025, 1, 1), "amount": 1}, path)
    orig = db.get_table("securities", path)
    db.sync_table("securities", orig, orig.iloc[0:0], path=path)
    assert db.get_table("trades", path).empty and db.get_table("dividends", path).empty
