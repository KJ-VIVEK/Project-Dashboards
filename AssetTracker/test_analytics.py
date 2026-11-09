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
                      "current_per_sqft": [None]})
    f = an.real_estate_frame(r).iloc[0]
    assert f.cost_per_sqft == 5000 and f.total_cost == 5_350_000 and f.value == 5_000_000
    assert f.rent_yield_pct == pytest.approx(240_000 / 5_350_000 * 100)


def test_ideal_is_income_times_percent():
    b = pd.DataFrame({"bucket": ["Gold", "Crypto"], "value": [300.0, 100.0], "invested": [0, 0]})
    a = an.allocation(b, {"Gold": 20, "Crypto": 50}, income=1000).set_index("bucket")
    assert a.loc["Gold", "target"] == 200 and a.loc["Crypto", "target"] == 500
    assert a.loc["Gold", "actual_pct"] == 30 and a.loc["Gold", "gap"] == -100 and a.loc["Crypto", "gap"] == 400
    assert an.allocation(b, {"Gold": 20}, income=0)["target"].sum() == 0


def test_usd_holding_uses_trade_fx_for_cost_and_live_fx_for_value():
    s = pd.DataFrame([{"id": 1, "name": "AAPL", "symbol": "AAPL", "category": "International - Stable",
                       "reason": "", "current_price": 200.0, "currency": "USD"}])
    t = pd.DataFrame([{"id": 1, "security_id": 1, "date": "2025-01-01", "side": "Buy", "qty": 10, "price": 100.0,
                       "fees": 10.0, "fx_rate": 80.0},
                      {"id": 2, "security_id": 1, "date": "2025-06-01", "side": "Buy", "qty": 10, "price": 150.0,
                       "fees": 0.0, "fx_rate": None}])
    d = pd.DataFrame(columns=["id", "security_id", "date", "amount"])
    pos, _, _ = an.build_positions(s, t, d, usd_inr=90.0)
    r = pos.iloc[0]
    assert r.invested == pytest.approx(1010 * 80 + 1500 * 90)      # 2nd trade has no rate -> today's
    assert r.avg_price == pytest.approx(2510 / 20)                  # native USD
    assert r.value == pytest.approx(20 * 200 * 90)                  # live rate
    assert r.pnl == pytest.approx(r.value - r.invested)


def test_usd_sale_realises_inr_gain_including_fx():
    s = pd.DataFrame([{"id": 1, "name": "X", "symbol": "", "category": "ETFs", "reason": "", "current_price": 1.0,
                       "currency": "USD"}])
    t = pd.DataFrame([{"id": 1, "security_id": 1, "date": "2025-01-01", "side": "Buy", "qty": 10, "price": 100.0,
                       "fees": 0.0, "fx_rate": 80.0},
                      {"id": 2, "security_id": 1, "date": "2025-02-01", "side": "Sell", "qty": 10, "price": 100.0,
                       "fees": 0.0, "fx_rate": 90.0}])
    d = pd.DataFrame(columns=["id", "security_id", "date", "amount"])
    _, realised, _ = an.build_positions(s, t, d, usd_inr=90.0)
    assert realised.iloc[0].pnl == pytest.approx(10 * 100 * 10) and realised.iloc[0].cost_sold == 80000


def test_money_flow_expense_is_income_minus_investments_and_savings():
    sec = pd.DataFrame([{"id": 1, "currency": "INR"}])
    tr = pd.DataFrame([
        {"id": 1, "security_id": 1, "date": "2025-01-01", "side": "Buy", "qty": 10, "price": 100.0, "fees": 0.0},
        {"id": 2, "security_id": 1, "date": "2025-03-01", "side": "Sell", "qty": 4, "price": 150.0, "fees": 0.0}])
    realised = pd.DataFrame([{"date": "2025-03-01", "security_id": 1, "pnl": 200.0, "cost_sold": 400.0}])
    flow = an.stock_net_invested(tr, sec, realised, 0)
    assert flow[2025] == 1000 - 400 and flow["all"] == 600
    metals = pd.DataFrame({"date": [date(2025, 5, 1), date(2024, 5, 1)], "invested": [100.0, 50.0]})
    re_ = pd.DataFrame({"purchase_date": [date(2025, 7, 1)], "total_cost": [1000.0]})
    sv = pd.DataFrame({"amount": [300.0, 200.0], "start_date": [date(2025, 2, 1), None]})
    y = an.money_flow(5000, flow, metals, re_, sv, 2025)
    assert y["invested"] == 600 + 100 + 1000 and y["saved"] == 300 and y["expense"] == 5000 - 1700 - 300
    allt = an.money_flow(5000, flow, metals, re_, sv, None)
    assert allt["invested"] == 600 + 150 + 1000 and allt["saved"] == 500


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
    db.set_target_pcts({"Gold": 25, "Crypto": 0, "ETFs": 500}, path)
    assert db.get_target_pcts(path) == {"Gold": 25.0, "ETFs": 100.0}


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


def test_migration_adds_columns_to_old_database(path):
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        old = os.path.join(d, "old.db")
        c = sqlite3.connect(old)
        c.executescript("CREATE TABLE securities (id INTEGER PRIMARY KEY, name TEXT NOT NULL UNIQUE, symbol TEXT, "
                        "category TEXT NOT NULL, reason TEXT, current_price REAL, price_date TEXT);"
                        "CREATE TABLE trades (id INTEGER PRIMARY KEY, security_id INTEGER NOT NULL, date TEXT NOT NULL,"
                        " side TEXT NOT NULL, qty REAL NOT NULL, price REAL NOT NULL, fees REAL, note TEXT);"
                        "INSERT INTO securities(name, category) VALUES ('A','ETFs');")
        c.commit(); c.close()
        db.init_db(old)
        db.init_db(old)  # idempotent
        assert db.get_table("securities", old).iloc[0]["currency"] in ("INR", None)
        assert "fx_rate" in db.get_table("trades", old).columns


def test_usd_inr_manual_override(path, monkeypatch):
    monkeypatch.setattr(db, "DB_PATH", path)
    db.meta_set("fx_usdinr", 88.5)
    assert db.usd_inr() == 88.5
    db.meta_set("fx_manual", 90)
    db.meta_set("fx_use_manual", "1")
    assert db.usd_inr() == 90


def test_blank_fees_and_currency_do_not_poison_numbers():
    s = pd.DataFrame([{"id": 1, "name": "U", "symbol": None, "category": "Unlisted Stocks", "reason": None,
                       "current_price": 12.0, "currency": None}])
    t = pd.DataFrame([{"id": 1, "security_id": 1, "date": "2025-01-01", "side": "Buy", "qty": 10, "price": 10.0,
                       "fees": None, "fx_rate": None}])
    d = pd.DataFrame(columns=["id", "security_id", "date", "amount"])
    pos, _, _ = an.build_positions(s, t, d, usd_inr=88.0)
    r = pos.iloc[0]
    assert r.invested == 100 and r.value == 120 and r.currency == "INR"
    assert an.stock_net_invested(t, s, pos.iloc[0:0].assign(date=[], cost_sold=[]), 88.0)["all"] == 100


def test_taxes_paid_are_subtracted_from_income():
    inc = pd.DataFrame({"date": [date(2025, 1, 31), date(2025, 1, 31), date(2025, 2, 28)],
                        "source": ["Salary", "Taxes Paid", "Taxes Paid"], "amount": [1000.0, 150.0, -50.0]})
    none = pd.DataFrame({"date": [], "amount": []})
    df = an.income_frame(inc, none, pd.DataFrame({"date": [], "pnl": []}))
    assert df["amount"].sum() == 1000 - 150 - 50
    p = an.income_pivot(df, 2025)
    assert p.loc["Taxes Paid", 1] == -150 and p.loc["Taxes Paid", 2] == -50 and p.to_numpy().sum() == 800


def test_real_estate_value_is_price_per_sqft_times_area():
    r = pd.DataFrame({"cost": [5_000_000, 3_000_000], "area_sqft": [1000, 0], "registration": [0, 0],
                      "brokerage": [0, 0], "other_charges": [0, 0], "rent_yielding": [False, False],
                      "monthly_rent": [None, None], "current_per_sqft": [7500, 9000]})
    f = an.real_estate_frame(r)
    assert f.value[0] == 7_500_000 and f.gain[0] == 2_500_000
    assert f.value[1] == 3_000_000          # no area -> cannot value by sq ft -> cost


def test_migration_converts_total_current_value_to_per_sqft():
    import sqlite3
    with tempfile.TemporaryDirectory() as d:
        old = os.path.join(d, "old.db")
        c = sqlite3.connect(old)
        c.executescript("CREATE TABLE real_estate (id INTEGER PRIMARY KEY, name TEXT NOT NULL, kind TEXT, "
                        "location TEXT, purchase_date TEXT, cost REAL NOT NULL, area_sqft REAL, registration REAL, "
                        "brokerage REAL, other_charges REAL, rent_yielding INTEGER NOT NULL DEFAULT 0, "
                        "monthly_rent REAL, current_value REAL, note TEXT);"
                        "INSERT INTO real_estate(name, cost, area_sqft, current_value) VALUES ('Flat', 1, 1000, 6900000);")
        c.commit(); c.close()
        db.init_db(old)
        assert db.get_table("real_estate", old).iloc[0]["current_per_sqft"] == 6900


def test_purity_factor():
    f = an.purity_factor
    assert f("Gold", "24K") == 1 and f("Gold", "22K") == pytest.approx(22 / 24) and f("Gold", "18K") == 0.75
    assert f("Gold", "916") == pytest.approx(0.916 / 0.999) and f("Silver", "925") == pytest.approx(0.925 / 0.999)
    assert f("Silver", "999") == 1 and f("Silver", "99.9%") == 1 and f("Silver", "99.99%") == 1
    assert f("Gold", None) == 1 and f("Gold", "junk") == 1 and f("Silver", "22K") == 1


def test_value_is_scaled_by_karat():
    m = pd.DataFrame({"metal": ["Gold", "Gold"], "purity": ["24K", "22K"], "grams": [10, 10],
                      "rate_per_g": [100, 100], "making_charges": [0, 0], "gst": [0, 0], "other_charges": [0, 0]})
    f = an.metals_frame(m, {"Gold": 240.0})
    assert list(f.value) == [2400, pytest.approx(2200)] and f.current_price[1] == pytest.approx(220)


def test_metal_premium_defaults_to_india_duty_until_set(path):
    assert db.metal_premium("Gold", path) == 6.0
    db.meta_set("premium_gold", 0, path)
    assert db.metal_premium("Gold", path) == 0.0


def test_closed_loan_owes_nothing_and_has_tenure():
    import datetime as dt
    l = pd.DataFrame([
        {"id": 1, "name": "A", "lender": "X", "link": None, "details": None, "category": "Worst", "principal": 200000.0, "outstanding": 5000.0, "interest_rate": 14.0,
         "emi": 9000.0, "start_date": dt.date(2024, 2, 1), "end_date": None, "status": "Closed",
         "closed_date": dt.date(2024, 11, 15)},
        {"id": 2, "name": "B", "lender": "Y", "link": None, "details": None, "category": "Good", "principal": 1e6, "outstanding": 8e5, "interest_rate": 8.0,
         "emi": 10000.0, "start_date": dt.date(2024, 1, 1), "end_date": dt.date(2034, 1, 1), "status": None,
         "closed_date": None}])
    f = an.loans_frame(l)
    assert f.loc[0, "outstanding"] == 0 and f.loc[0, "emi"] == 0 and f.loc[0, "repaid_pct"] == 100
    assert f.loc[0, "tenure_months"] == 9
    assert f.loc[1, "status"] == "Active" and f.loc[1, "outstanding"] == 8e5 and f.loc[1, "tenure_months"] == 120
    assert "Closed" in an.loans_markdown(l) and "9 months" in an.loans_markdown(l)


def test_emi_schedule_interest_saved():
    r = an.emi_schedule(1_000_000, 12, 12, 12)          # known: EMI 88,848.79 for 1L @12% x12 -> 1M principal
    assert round(r["emi"], 2) == 88848.79
    assert round(r["interest_full"], 0) == round(88848.79 * 12 - 1_000_000, 0)
    assert r["interest_saved"] == pytest.approx(0, abs=1e-6) and r["balance_cleared"] == pytest.approx(0, abs=1e-6)
    early = an.emi_schedule(1_000_000, 12, 12, 6)       # closed after 6 of 12 EMIs
    assert early["interest_paid"] < early["interest_full"] and early["interest_saved"] > 0
    # first 6 months of interest + nothing else: balance after 6 EMIs matches the amortisation table
    bal = 1_000_000
    interest = 0
    for _ in range(6):
        i = bal * 0.01
        interest += i
        bal -= 88848.79 - i
    assert early["balance_cleared"] == pytest.approx(bal, abs=1)
    assert early["interest_paid"] == pytest.approx(interest, abs=1)
    assert an.emi_schedule(1e6, 0, 10, 4)["interest_full"] == 0
    assert an.emi_schedule(None, 12, 12, 6) is None


def test_closed_loan_with_actuals_reconciles():
    import datetime as dt
    l = pd.DataFrame([{"id": 1, "name": "Bike", "lender": "BF", "link": None, "details": None, "category": "Bad",
                       "principal": 273000.0, "outstanding": 0.0, "interest_rate": 16.5, "emi": 6734.0,
                       "start_date": dt.date(2023, 11, 30), "end_date": dt.date(2028, 11, 30), "status": "Closed",
                       "closed_date": dt.date(2024, 9, 30), "down_payment": 150000.0, "other_charges": 11179.0,
                       "installments_paid": 8, "emi_paid": 54756.0, "emi_interest": 29043.0,
                       "closing_payment": 258466.0}])
    r = an.loans_frame(l).iloc[0]
    assert r["emi_principal"] == 25713 and r["balance_cleared"] == 247287
    assert r["closing_gap"] == 0                                  # 247,287 + 11,179 = 258,466
    assert r["asset_cost"] == 423000
    assert r["total_paid"] - r["asset_cost"] == r["cost_of_borrowing"] == 29043 + 11179
    assert r["interest_saved"] > 0 and r["net_saved"] == r["interest_saved"] - 11179


# ---------------------------------------------------------------- loan calculations, one worked case end to end
KTM = {"id": 1, "name": "KTM", "lender": "BF", "link": None, "details": None, "category": "Bad",
       "principal": 273000.0, "outstanding": 0.0, "interest_rate": 16.5, "emi": 6734.0,
       "start_date": date(2023, 11, 30), "end_date": date(2028, 11, 30), "status": "Closed",
       "closed_date": date(2024, 9, 30), "down_payment": 150000.0, "other_charges": 11179.0,
       "installments_paid": 8, "emi_paid": 7 * 6734 + 7618, "emi_interest": 29043.0, "closing_payment": 258466.0}


def _amortise(P, annual, emi, k):
    bal, interest = P, 0.0
    for _ in range(k):
        i = bal * annual / 1200
        interest += i
        bal -= emi - i
    return interest, bal


@pytest.mark.parametrize("a,b,expected", [
    (date(2023, 11, 30), date(2024, 9, 30), 10),     # same day number
    (date(2024, 1, 15), date(2024, 2, 14), 0),       # a day short of a month
    (date(2024, 1, 15), date(2024, 2, 15), 1),
    (date(2024, 1, 31), date(2024, 2, 29), 1),       # month-end to shorter month-end completes the month
    (date(2024, 1, 30), date(2024, 3, 29), 1),       # 29 Mar is not a month-end, so only one full month
    (date(2023, 11, 30), date(2028, 11, 30), 60),
    (date(2024, 5, 1), date(2024, 4, 1), 0),         # never negative
    (None, date(2024, 1, 1), None)])
def test_whole_months(a, b, expected):
    assert an.whole_months(a, b) == expected


def test_emi_matches_the_standard_formula_and_an_amortisation_table():
    P, rate, n = 273000, 16.5, 60
    s = an.emi_schedule(P, rate, n, 8)
    i = rate / 1200
    assert s["emi"] == pytest.approx(P * i * (1 + i) ** n / ((1 + i) ** n - 1))
    assert s["emi"] == pytest.approx(6711.57, abs=0.01)
    interest, bal = _amortise(P, rate, s["emi"], 8)
    assert s["balance_cleared"] == pytest.approx(bal, abs=0.01)
    assert s["interest_paid"] == pytest.approx(interest + 0, abs=0.01)         # no charges inside this figure
    assert s["interest_full"] == pytest.approx(s["emi"] * n - P)
    full_i, full_bal = _amortise(P, rate, s["emi"], n)
    assert full_bal == pytest.approx(0, abs=0.01) and full_i == pytest.approx(s["interest_full"], abs=0.01)


def test_ktm_loan_every_figure():
    r = an.loans_frame(pd.DataFrame([KTM])).iloc[0]
    assert r["tenure_months"] == 8 and r["elapsed_months"] == 10 and r["planned_months"] == 60
    assert r["outstanding"] == 0 and r["emi"] == 0 and r["repaid_pct"] == 100
    assert r["asset_cost"] == 423000
    assert r["emi_principal"] == 25713
    assert r["balance_cleared"] == 247287                     # 273,000 - 25,713
    assert r["closing_gap"] == 0                              # 247,287 + 11,179 = 258,466
    assert r["interest_paid"] == 29043 and r["cost_of_borrowing"] == 40222
    assert r["total_paid"] == 463222 and r["total_paid"] - r["asset_cost"] == r["cost_of_borrowing"]
    assert r["interest_full"] == pytest.approx(129694.46, abs=0.01)
    assert r["interest_saved"] == pytest.approx(129694.46 - 29043, abs=0.01)
    assert r["net_saved"] == pytest.approx(r["interest_saved"] - 11179, abs=0.01)
    # the calculator's estimate for the same 8 installments lands within 1% of what was really paid
    assert r["interest_est"] == pytest.approx(29043, rel=0.01)
    assert r["balance_est"] == pytest.approx(247287, rel=0.01)


def test_tenure_falls_back_to_dates_without_installments_and_markdown_reports_it():
    k = dict(KTM, installments_paid=None, emi_paid=None, emi_interest=None, closing_payment=None)
    r = an.loans_frame(pd.DataFrame([k])).iloc[0]
    assert r["tenure_months"] == 10 and pd.isna(r["interest_paid"]) is False   # estimated from the calculator
    md = an.loans_markdown(pd.DataFrame([KTM]))
    assert "Tenure: 8" in md and "40,222" in md


def test_active_loan_is_untouched_by_closing_maths():
    a = dict(KTM, status="Active", closed_date=None, outstanding=150000.0, installments_paid=None, emi_paid=None,
             emi_interest=None, closing_payment=None, down_payment=None, other_charges=None)
    r = an.loans_frame(pd.DataFrame([a])).iloc[0]
    assert r["outstanding"] == 150000 and r["emi"] == 6734 and pd.isna(r["interest_saved"])
    assert pd.isna(r["balance_cleared"]) and r["other_charges"] == 0


def test_unrecorded_repayment_figures_stay_unknown_not_zero():
    k = dict(KTM, installments_paid=None, emi_paid=None, emi_interest=None, closing_payment=None, down_payment=None,
             other_charges=None)
    r = an.loans_frame(pd.DataFrame([k])).iloc[0]
    assert pd.isna(r["emi_principal"]) and pd.isna(r["total_paid"]) and pd.isna(r["closing_gap"])
    assert r["balance_cleared"] == pytest.approx(r["balance_est"]) and r["balance_cleared"] > 200000
    assert r["interest_paid"] == pytest.approx(r["interest_est"]) and r["other_charges"] == 0


def test_paid_to_lender_minus_loan_is_interest_plus_charges():
    r = an.loans_frame(pd.DataFrame([KTM])).iloc[0]
    assert r["lender_paid"] == 313222 and r["lender_extra"] == 40222
    assert r["lender_extra"] == r["interest_paid"] + r["other_charges"]


def test_blank_text_fields_do_not_leak_nan():
    k = dict(KTM, lender=None, link=float("nan"), details=None)
    md = an.loans_markdown(pd.DataFrame([k]))
    assert "nan" not in md.lower() and "Lender: –" in md and "Documents" not in md


# ---------------------------------------------------------------- insurance: start/end dates and premium changes
TODAY = date(2026, 11, 9)


def _policy(**kw):
    base = {"id": 1, "name": "P", "premium": 1800.0, "frequency": "Monthly", "active": True,
            "start_date": None, "end_date": None}
    return pd.DataFrame([{**base, **kw}])


def test_monthly_policy_part_year_is_premium_times_months_over_twelve():
    # 1,800 a month = 21,600 a year; in force Jul-Dec 2026 (6 months) -> 21,600 x 6 / 12 = 10,800
    f = an.insurance_frame(_policy(start_date=date(2026, 7, 15)), year=2026, today=TODAY).iloc[0]
    assert f["months_in_year"] == 6 and f["year_cost"] == pytest.approx(10800)
    assert f["per_year"] == 21600 and f["per_month"] == 1800


def test_end_date_stops_the_cost_and_full_year_equals_annual_premium():
    p = _policy(start_date=date(2025, 3, 1), end_date=date(2026, 4, 30))
    assert an.insurance_frame(p, year=2025, today=TODAY).iloc[0]["year_cost"] == pytest.approx(1800 * 10)   # Mar-Dec
    assert an.insurance_frame(p, year=2026, today=TODAY).iloc[0]["year_cost"] == pytest.approx(1800 * 4)    # Jan-Apr
    assert an.insurance_frame(p, year=2027, today=TODAY).iloc[0]["year_cost"] == 0
    f = an.insurance_frame(p, year=2026, today=TODAY).iloc[0]
    assert f["per_year"] == 0                                           # ended: costs nothing going forward
    assert f["paid_to_date"] == pytest.approx(1800 * 14) and f["months_to_date"] == 14
    full = an.insurance_frame(_policy(start_date=date(2020, 1, 1)), year=2026, today=TODAY).iloc[0]
    assert full["year_cost"] == pytest.approx(21600) and full["months_in_year"] == 12


def test_yearly_and_quarterly_are_spread_per_month_not_paid_in_a_lump():
    y = an.insurance_frame(_policy(premium=12000.0, frequency="Yearly", start_date=date(2026, 10, 1)),
                           year=2026, today=TODAY).iloc[0]
    assert y["year_cost"] == pytest.approx(12000 * 3 / 12)              # Oct-Dec
    q = an.insurance_frame(_policy(premium=3000.0, frequency="Quarterly", start_date=date(2026, 1, 1)),
                           year=2026, today=TODAY).iloc[0]
    assert q["year_cost"] == pytest.approx(12000) and q["per_month"] == 1000


def test_premium_change_applies_from_its_month_and_keeps_the_policy():
    term = _policy(premium=18000.0, frequency="Yearly", start_date=date(2022, 4, 1))
    revs = pd.DataFrame([{"id": 1, "insurance_id": 1, "effective_from": date(2026, 4, 1), "premium": 15000.0,
                          "note": "GST change"}])
    f = an.insurance_frame(term, revs, year=2026, today=TODAY).iloc[0]
    assert f["premium_now"] == 15000 and f["per_year"] == 15000 and f["per_month"] == 1250
    assert f["year_cost"] == pytest.approx(18000 * 3 / 12 + 15000 * 9 / 12)        # Jan-Mar old, Apr-Dec new
    assert an.insurance_frame(term, revs, year=2025, today=TODAY).iloc[0]["year_cost"] == pytest.approx(18000)
    # paid to date: Apr 2022 .. Mar 2026 = 48 months at 1,500 + Apr..Nov 2026 = 8 months at 1,250
    assert f["months_to_date"] == 56 and f["paid_to_date"] == pytest.approx(48 * 1500 + 8 * 1250)
    # another policy's revisions never leak across
    other = pd.concat([term, _policy(id=2, premium=500.0)], ignore_index=True)
    assert an.insurance_frame(other, revs, year=2026, today=TODAY).iloc[1]["premium_now"] == 500


def test_lapsed_without_end_date_counts_nothing_and_missing_start_is_unknown():
    f = an.insurance_frame(_policy(active=False), year=2026, today=TODAY).iloc[0]
    assert f["year_cost"] == 0 and f["per_year"] == 0
    g = an.insurance_frame(_policy(), year=2026, today=TODAY).iloc[0]       # no dates: whole year, no history
    assert g["year_cost"] == pytest.approx(21600) and pd.isna(g["paid_to_date"])


def test_deleting_a_policy_removes_its_premium_changes(path):
    pid = db.insert("insurance", {"name": "Term", "premium": 10, "frequency": "Yearly", "active": True}, path)
    db.insert("insurance_revisions", {"insurance_id": pid, "effective_from": date(2026, 1, 1), "premium": 9}, path)
    db.delete_row("insurance", pid, path)
    assert db.get_table("insurance_revisions", path).empty


def test_policy_status_active_inactive_not_started():
    ins = pd.concat([_policy(id=1, start_date=date(2025, 1, 1)),
                     _policy(id=2, start_date=date(2024, 1, 1), end_date=date(2025, 12, 31)),
                     _policy(id=3, active=False),
                     _policy(id=4, start_date=date(2027, 1, 1))], ignore_index=True)
    f = an.insurance_frame(ins, today=TODAY)
    assert list(f["status"]) == ["Active", "Inactive", "Inactive", "Not started"]
    assert list(f["per_year"] == 0) == [False, True, True, True]


def test_premiums_paid_are_tracked_monthly_and_subtracted_from_income():
    term = _policy(premium=18000.0, frequency="Yearly", start_date=date(2026, 8, 20))
    revs = pd.DataFrame([{"id": 1, "insurance_id": 1, "effective_from": date(2026, 10, 1), "premium": 15000.0,
                          "note": None}])
    pay = an.insurance_payments(term, revs, today=TODAY)
    assert list(pay["date"]) == [date(2026, 8, 1), date(2026, 9, 1), date(2026, 10, 1), date(2026, 11, 1)]
    assert list(pay["amount"]) == [1500, 1500, 1250, 1250]
    assert pay["amount"].sum() == an.insurance_frame(term, revs, today=TODAY).iloc[0]["paid_to_date"]
    assert an.insurance_payments(_policy(), None, today=TODAY).empty            # no start date, no history
    inc = pd.DataFrame({"date": [date(2026, 8, 5)], "source": ["Salary"], "amount": [100000.0]})
    empty = pd.DataFrame(columns=["date", "amount", "pnl"])
    f = an.income_frame(inc, empty, empty, True, pay)
    assert f["amount"].sum() == 100000 - 5500
    assert set(f["source"]) == {"Salary", db.PREMIUM_SOURCE} and (f[f.source == db.PREMIUM_SOURCE]["amount"] < 0).all()
    assert an.income_pivot(f, None, by="year").loc[db.PREMIUM_SOURCE, 2026] == -5500
    assert an.income_frame(inc, empty, empty, True)["amount"].sum() == 100000    # off by default


# ---------------------------------------------------------------- vehicle insurance: OD + TP as covers of one policy
def _vehicle(today=TODAY):
    od_old = {"id": 2, "parent_id": 1, "component": "Own Damage (OD)", "name": "Bike - OD", "premium": 6000.0,
              "frequency": db.ONE_TIME, "start_date": date(2024, 3, 10), "end_date": date(2025, 3, 9), "active": True}
    od_new = {"id": 3, "parent_id": 1, "component": "Own Damage (OD)", "name": "Bike - OD", "premium": 7200.0,
              "frequency": db.ONE_TIME, "start_date": date(2026, 3, 10), "end_date": date(2027, 3, 9), "active": True}
    tp = {"id": 4, "parent_id": 1, "component": "Third Party (TP)", "name": "Bike - TP", "premium": 6000.0,
          "frequency": db.ONE_TIME, "start_date": date(2024, 3, 10), "end_date": date(2029, 3, 9), "active": True}
    parent = {"id": 1, "parent_id": None, "component": None, "name": "Bike", "premium": 0.0, "frequency": "Yearly",
              "start_date": None, "end_date": None, "active": True, "sum_assured": 90000.0}
    return pd.DataFrame([parent, od_old, od_new, tp])


def test_one_year_policy_covers_twelve_months_not_thirteen():
    p = _policy(premium=12000.0, frequency=db.ONE_TIME, start_date=date(2025, 3, 10), end_date=date(2026, 3, 9))
    f = an.insurance_frame(p, year=2025, today=TODAY).iloc[0]
    assert f["months_in_year"] == 10 and f["year_cost"] == pytest.approx(10000)      # Mar..Dec at 1,000 a month
    assert an.insurance_frame(p, year=2026, today=TODAY).iloc[0]["year_cost"] == pytest.approx(2000)   # Jan, Feb
    assert an.insurance_frame(p, today=TODAY).iloc[0]["paid_to_date"] == pytest.approx(12000)          # fully paid
    assert an.insurance_frame(p, today=TODAY).iloc[0]["status"] == "Inactive"


def test_od_is_renewed_yearly_and_tp_runs_long_as_covers_of_one_policy():
    v = an.insurance_frame(_vehicle(), year=2026, today=TODAY)
    od_old, od_new, tp = v.iloc[1], v.iloc[2], v.iloc[3]
    assert od_old["status"] == "Inactive" and od_new["status"] == "Active" and tp["status"] == "Active"
    assert tp["per_year"] == pytest.approx(6000 / 60 * 12)                            # 5-year premium, per year 1,200
    assert od_new["per_year"] == pytest.approx(7200)
    assert od_old["paid_to_date"] == pytest.approx(6000)
    assert od_new["paid_to_date"] == pytest.approx(7200 / 12 * 9)                     # Mar..Nov 2026
    assert tp["paid_to_date"] == pytest.approx(6000 / 60 * 33)                        # Mar 2024..Nov 2026
    # 2026: TP all 12 months at 100, new OD Mar-Dec (10 months at 600), old OD ended in Feb 2025 so nothing
    assert v["year_cost"].sum() == pytest.approx(1200 + 6000)
    assert v.iloc[0]["per_year"] == 0                                                # the parent adds nothing itself
    pol = an.insurance_policies(v)
    assert len(pol) == 1 and pol.iloc[0]["name"] == "Bike" and pol.iloc[0]["status"] == "Active"
    assert pol.iloc[0]["paid_to_date"] == pytest.approx(6000 + 5400 + 3300)
    assert pol.iloc[0]["per_year"] == pytest.approx(1200 + 7200) and pol.iloc[0]["n_covers"] == 3
    assert "Third Party (TP)" in pol.iloc[0]["covers"] and "Own Damage (OD)" in pol.iloc[0]["covers"]


def test_policy_is_inactive_only_when_every_cover_has_ended_and_premiums_reach_income():
    v = _vehicle()
    v.loc[v["id"] != 1, "end_date"] = date(2026, 1, 31)
    pol = an.insurance_policies(an.insurance_frame(v, today=TODAY))
    assert pol.iloc[0]["status"] == "Inactive" and pol.iloc[0]["per_year"] == 0
    pay = an.insurance_payments(_vehicle(), None, today=TODAY)
    assert pay["amount"].sum() == pytest.approx(6000 + 5400 + 3300)


def test_deleting_a_policy_removes_its_covers(path):
    pid = db.insert("insurance", {"name": "Bike", "premium": 0, "frequency": "Yearly", "active": True}, path)
    cid = db.insert("insurance", {"name": "Bike - TP", "premium": 5, "frequency": db.ONE_TIME, "active": True,
                                  "parent_id": pid, "component": "Third Party (TP)"}, path)
    db.insert("insurance_revisions", {"insurance_id": cid, "effective_from": date(2026, 1, 1), "premium": 6}, path)
    db.delete_row("insurance", pid, path)
    assert db.get_table("insurance", path).empty and db.get_table("insurance_revisions", path).empty


def test_a_renewal_that_has_not_started_does_not_double_the_current_rate():
    v = _vehicle()
    v = pd.concat([v, pd.DataFrame([{**v.iloc[2].to_dict(), "id": 5, "premium": 9999.0,
                                     "start_date": date(2027, 3, 10), "end_date": date(2028, 3, 9)}])],
                  ignore_index=True)
    f = an.insurance_frame(v, today=TODAY)
    assert f.iloc[4]["status"] == "Not started" and f.iloc[4]["per_year"] == 0
    assert an.insurance_policies(f).iloc[0]["per_year"] == pytest.approx(1200 + 7200)
