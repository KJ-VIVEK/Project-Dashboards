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
    ed.loc[len(ed)] = [None, date(2025, 2, 1), "Misc", "gift", 5.0, None, None, None, None, None, None, None, None, None]       # add
    ed.loc[len(ed)] = [None, None, None, None, None, None, None, None, None, None, None, None, None, None]                      # blank: ignored
    ed.loc[len(ed)] = [None, date(2025, 3, 1), "Salary", None, None, None, None, None, None, None, None, None, None, None]      # no amount: skipped
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
    # actually paid: 18,000 on 1 Apr 2022, 2023, 2024, 2025 and the new 15,000 on 1 Apr 2026 (a yearly premium is paid whole)
    assert f["months_to_date"] == 56 and f["paid_to_date"] == pytest.approx(4 * 18000 + 15000)
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
    assert list(pay["date"]) == [date(2026, 8, 20)] and list(pay["amount"]) == [18000]   # one yearly payment, in August
    assert pay["amount"].sum() == an.insurance_frame(term, revs, today=TODAY).iloc[0]["paid_to_date"]
    assert an.insurance_payments(_policy(), None, today=TODAY).empty            # no start date, no history
    inc = pd.DataFrame({"date": [date(2026, 8, 5)], "source": ["Salary"], "amount": [100000.0]})
    empty = pd.DataFrame(columns=["date", "amount", "pnl"])
    f = an.income_frame(inc, empty, empty, True, pay)
    assert f["amount"].sum() == 100000 - 18000
    assert set(f["source"]) == {"Salary", db.PREMIUM_SOURCE} and (f[f.source == db.PREMIUM_SOURCE]["amount"] < 0).all()
    assert an.income_pivot(f, None, by="year").loc[db.PREMIUM_SOURCE, 2026] == -18000
    assert an.income_frame(inc, empty, empty, True)["amount"].sum() == 100000    # off by default


def test_one_year_policy_covers_twelve_months_not_thirteen():
    p = _policy(premium=12000.0, frequency=db.ONE_TIME, start_date=date(2025, 3, 10), end_date=date(2026, 3, 9))
    f = an.insurance_frame(p, year=2025, today=TODAY).iloc[0]
    assert f["months_in_year"] == 10 and f["year_cost"] == pytest.approx(10000)      # Mar..Dec at 1,000 a month
    assert an.insurance_frame(p, year=2026, today=TODAY).iloc[0]["year_cost"] == pytest.approx(2000)   # Jan, Feb
    assert an.insurance_frame(p, today=TODAY).iloc[0]["paid_to_date"] == pytest.approx(12000)          # fully paid
    assert an.insurance_frame(p, today=TODAY).iloc[0]["status"] == "Inactive"


def test_vehicle_od_and_tp_are_ordinary_policies_with_an_insured_label():
    """Own Damage (renewed yearly) and Third Party (multi-year) are simply two policies, one per term."""
    od = {"premium": 6000.0, "frequency": db.ONE_TIME, "start_date": date(2025, 3, 10), "end_date": date(2026, 3, 9),
          "insured_for": "Own Damage (OD)"}
    od2 = {**od, "premium": 7200.0, "start_date": date(2026, 3, 10), "end_date": date(2027, 3, 9)}
    tp = {"premium": 6000.0, "frequency": db.ONE_TIME, "start_date": date(2025, 3, 10), "end_date": date(2030, 3, 9),
          "insured_for": "Third Party (TP)"}
    ins = pd.concat([_policy(id=1, **od), _policy(id=2, **od2), _policy(id=3, **tp)], ignore_index=True)
    f = an.insurance_frame(ins, year=2026, today=TODAY)
    assert list(f["status"]) == ["Inactive", "Active", "Active"]
    assert f["paid_to_date"].round(2).tolist() == [6000, 7200, 6000]       # one-time premiums are paid whole on the start date
    assert f["per_year"].tolist() == [0, 7200, 1200]                                           # TP 6,000 / 5 years
    assert f["year_cost"].sum() == pytest.approx(7200 / 12 * 10 + 1200 + 6000 / 12 * 2)         # new OD, TP, old OD Jan-Feb
    assert db.INSURED_FOR[:2] == ["Me", "Family"] and "Third Party (TP)" in db.INSURED_FOR



def test_a_policy_starting_later_this_month_has_not_started_and_has_paid_nothing():
    f = an.insurance_frame(_policy(start_date=date(2026, 11, 25)), today=TODAY).iloc[0]       # TODAY is 9 Nov 2026
    assert f["status"] == "Not started" and f["paid_to_date"] == 0 and f["per_year"] == 0
    assert an.insurance_payments(_policy(start_date=date(2026, 11, 25)), None, today=TODAY).empty
    g = an.insurance_frame(_policy(start_date=date(2026, 11, 9)), today=TODAY).iloc[0]       # starts today
    assert g["status"] == "Active" and g["paid_to_date"] == 1800


def test_status_follows_real_dates_not_whole_months():
    """25 Nov 2025 - 24 Nov 2026 is still running on 9 Nov 2026 (its last *counted* month is October)."""
    p = _policy(start_date=date(2025, 11, 25), end_date=date(2026, 11, 24))
    f = an.insurance_frame(p, today=TODAY).iloc[0]
    assert f["status"] == "Active" and f["per_year"] == 21600
    assert f["paid_to_date"] == pytest.approx(1800 * 12)                  # twelve months counted, never thirteen
    assert an.insurance_frame(p, today=date(2026, 11, 24)).iloc[0]["status"] == "Active"     # last day
    assert an.insurance_frame(p, today=date(2026, 11, 25)).iloc[0]["status"] == "Inactive"   # the day after


def test_paid_follows_the_payment_dates_for_every_frequency():
    d0 = date(2026, 1, 31)
    cases = {"Monthly": 10, "Quarterly": 4, "Half-Yearly": 2, "Yearly": 1}       # payments made by 9 Nov 2026
    for freq, n in cases.items():
        pay = an.insurance_payments(_policy(premium=100.0, frequency=freq, start_date=d0), None, today=TODAY)
        assert len(pay) == n and pay["amount"].sum() == 100 * n, freq
    pay = an.insurance_payments(_policy(premium=100.0, frequency="Monthly", start_date=d0), None, today=TODAY)
    assert pay["date"].iloc[1] == date(2026, 2, 28)                           # month-end is clamped, then back to the 31st
    assert pay["date"].iloc[2] == date(2026, 3, 31)
    ended = _policy(premium=100.0, frequency="Monthly", start_date=d0, end_date=date(2026, 4, 30))
    assert len(an.insurance_payments(ended, None, today=TODAY)) == 4          # Jan 31, Feb 28, Mar 31, Apr 30
    y = an.insurance_frame(_policy(premium=14379.0, frequency="Yearly", start_date=date(2026, 8, 1)), today=TODAY).iloc[0]
    assert y["paid_to_date"] == 14379 and y["year_cost"] == pytest.approx(14379 * 5 / 12)    # paid whole; cost spread


# ---------------------------------------------------------------- validation of edits
import validation as val


def _df(**cols):
    return pd.DataFrame({k: [v] for k, v in cols.items()})


def test_valid_rows_give_no_errors():
    assert val.validate("insurance", _df(id=1, name="Term", premium=1000.0, frequency="Yearly", type="Term Life",
                                         start_date=date(2025, 1, 1), end_date=date(2045, 1, 1),
                                         insured_for="Me", sum_assured=1e7), ("name", "premium", "frequency")) == []
    assert val.validate("income", _df(id=1, date=date(2026, 1, 5), source="PL Booking", amount=-500.0)) == []


def test_missing_required_values_are_reported_not_skipped():
    msgs = val.validate("insurance", _df(id=1, name="Term", premium=None, frequency=None),
                        ("name", "premium", "frequency"))
    assert any("Premium is required" in m for m in msgs) and any("Paid is required" in m for m in msgs)
    assert all(m.startswith("Row 1 (Term):") for m in msgs)
    assert val.validate("trades", _df(id=1, security_id=float("nan"), date=date(2026, 1, 1), side="Buy", qty=1, price=1),
                        ("security_id", "date", "side", "qty", "price")) == ["Row 1: Holding is required."]


def test_wrong_values_are_reported_in_words():
    m = val.validate("insurance", _df(id=1, name="Term", premium=-5.0, frequency="Fortnightly", sum_assured=-1.0,
                                      insured_for="Cat"))
    text = " ".join(m)
    assert "Premium must be above 0 (got -5)" in text and "Paid “Fortnightly” is not one of" in text
    assert "Sum assured cannot be negative" in text and "Insured “Cat”" in text
    assert any("must be a number" in x for x in val.validate("loans", _df(id=1, name="L", principal="abc")))
    assert any("between 0 and 100" in x for x in val.validate("loans", _df(id=1, name="L", interest_rate=250.0)))
    assert any("whole number" in x for x in val.validate("loans", _df(id=1, name="L", installments_paid=2.5)))
    assert not val.validate("income", _df(id=1, source="Salary", amount=-1.0))      # losses are allowed on any source
    assert any("positive amount" in x for x in val.validate("income", _df(id=1, source="Taxes Paid", amount=-1.0)))
    assert any("cannot be zero" in x for x in val.validate("income", _df(id=1, source="Salary", amount=0.0)))
    assert any("Purity “925” does not exist for Gold" in x
               for x in val.validate("metals", _df(id=1, metal="Gold", purity="925", grams=1.0, rate_per_g=1.0)))


def test_dates_must_be_real_in_range_and_in_order():
    m = val.validate("insurance", _df(id=1, name="P", premium=1.0, frequency="Yearly",
                                      start_date=date(2026, 5, 1), end_date=date(2026, 1, 1)))
    assert m == ["Row 1 (P): End date (2026-01-01) is before start date (2026-05-01)."]
    assert any("outside 1995-2100" in x for x in val.validate("loans", _df(id=1, name="L", start_date=date(2150, 1, 1))))
    assert any("not a valid date" in x for x in val.validate("loans", _df(id=1, name="L", start_date="soon")))
    assert any("Closed on" in x and "before" in x for x in val.validate(
        "loans", _df(id=1, name="L", start_date=date(2025, 5, 1), closed_date=date(2025, 1, 1))))
    assert any("needs both a start and an end" in x for x in val.validate(
        "insurance", _df(id=1, name="P", premium=1.0, frequency=db.ONE_TIME, start_date=date(2026, 1, 1))))
    assert any("Interest in the installments" in x for x in val.validate(
        "loans", _df(id=1, name="L", emi_paid=100.0, emi_interest=200.0)))


def test_every_editable_table_has_rules_and_row_numbers_count_from_one():
    for t in ("income", "savings", "insurance", "insurance_revisions", "loans", "securities", "trades", "dividends",
              "metals", "real_estate"):
        assert t in db.TABLES and val.validate(t, pd.DataFrame(columns=["id"])) == []
    two = pd.DataFrame([{"id": 1, "name": "A", "premium": 5.0, "frequency": "Yearly"},
                        {"id": 2, "name": "B", "premium": 0.0, "frequency": "Yearly"}])
    assert val.validate("insurance", two) == ["Row 2 (B): Premium must be above 0 (got 0)."]


def test_epf_nps_or_returned_amounts_are_recorded_but_not_subtracted():
    inc = pd.DataFrame({"date": [date(2025, 1, 31)], "source": ["Salary"], "amount": [1000.0], "tax_paid": [100.0],
                        "other_deductions": [40.0]})
    none = pd.DataFrame({"date": [], "amount": []})
    df = an.income_frame(inc, none, pd.DataFrame({"date": [], "pnl": []}))
    assert df["amount"].sum() == 900                                  # only the tax comes off
    assert "Other Deductions" not in set(df["source"])


def test_deductions_on_the_inflow_row_are_subtracted_on_its_date():
    inc = pd.DataFrame({"date": [date(2025, 3, 31)], "source": ["Salary"], "amount": [1000.0],
                        "tax_paid": [100.0], "other_deductions": [None]})
    none = pd.DataFrame({"date": [], "amount": []})
    df = an.income_frame(inc, none, pd.DataFrame({"date": [], "pnl": []}))
    assert df["amount"].sum() == 900
    p = an.income_pivot(df, 2025)
    assert p.loc["Salary", 3] == 1000 and p.loc["Taxes Paid", 3] == -100
    assert val.validate("income", pd.DataFrame({"id": [1], "date": [date(2025, 3, 31)], "source": ["Salary"],
                                                "amount": [1000.0], "tax_paid": [-5.0]}))


def test_init_db_survives_a_column_added_by_another_session(path):
    db.init_db(path)
    import sqlite3
    c = sqlite3.connect(path)
    c.execute("ALTER TABLE income DROP COLUMN other_deductions")
    c.commit()
    c.close()
    real = db._MIGRATIONS
    db._MIGRATIONS = list(real)
    db.init_db(path)            # adds it again
    db.init_db(path)            # and a repeat run is harmless
    db._MIGRATIONS = real
    assert "other_deductions" in db.get_table("income", path).columns


# ---- Source sheet totals: every figure checked against a plain hand sum ------------------------------------
def _source_total_row(df, year=None, by="month"):
    """The Source tab's table exactly as app.py builds it: the pivot plus a Total column and a Total row."""
    pv = an.income_pivot(df, year, by=by)
    pv["Total"] = pv.sum(axis=1)
    pv.loc["Total"] = pv.sum()
    return pv


def _empty_auto():
    return pd.DataFrame({"date": [], "amount": []}), pd.DataFrame({"date": [], "pnl": []})


def _src(rows):
    cols = ["date", "source", "description", "amount", "tax_paid", "other_deductions"]
    return pd.DataFrame(rows, columns=cols)


def test_source_totals_match_a_hand_sum():
    inc = _src([
        [date(2025, 1, 28), "Salary", "A", 100000.0, 10000.0, 5000.0],     # EPF/NPS 5,000 is not subtracted
        [date(2025, 2, 28), "Salary", "A", 100000.0, 10000.0, None],       
        [date(2025, 2, 10), "Miscellaneous", "bonus", 20000.0, None, 0.0],  
        [date(2026, 1, 28), "Salary", "A", 50000.0, 2500.0, 500.0],        
    ])
    d, r = _empty_auto()
    df = an.income_frame(inc, d, r)
    m25 = _source_total_row(df, 2025)
    assert m25.loc["Salary", "Total"] == 200000                    # gross salary
    assert m25.loc["Taxes Paid", "Total"] == -20000
    assert m25.loc["Total", 1] == 90000 and m25.loc["Total", 2] == 110000     # Jan 100k-10k; Feb 100k+20k bonus-10k
    assert m25.loc["Total", "Total"] == 200000 + 20000 - 20000 == 200000
    y = _source_total_row(df, None, by="year")
    assert y.loc["Total", 2025] == 200000 and y.loc["Total", 2026] == 47500
    assert y.loc["Total", "Total"] == 247500                       # the sum of all years
    assert y.loc["Total", "Total"] == m25.loc["Total", "Total"] + 47500


def test_source_row_and_column_totals_agree():
    inc = _src([[date(2024, 3, 1), "Salary", "x", 1000.0, 100.0, 50.0], [date(2024, 7, 1), "PL Booking", "t", -300.0, None, None]])
    d, r = _empty_auto()
    pv = _source_total_row(an.income_frame(inc, d, r), 2024)
    body = pv.drop(index="Total", columns="Total")
    assert pv.loc["Total", "Total"] == body.to_numpy().sum() == 1000 - 100 - 300
    assert (pv["Total"].drop("Total") == body.sum(axis=1)).all()
    assert (pv.loc["Total"].drop("Total") == body.sum(axis=0)).all()


def test_source_deduction_edge_cases():
    inc = _src([
        [date(2025, 5, 1), "Salary", "s", 1000.0, -100.0, None],            # a deduction typed negative still subtracts once
        [date(2025, 5, 2), "Salary", "s", 1000.0, float("nan"), float("nan")],   # blank deductions add nothing
        [date(2025, 5, 3), "Taxes Paid", "old style row", 70.0, None, None],     # a legacy separate row still subtracts
    ])
    d, r = _empty_auto()
    df = an.income_frame(inc, d, r)
    assert df["amount"].sum() == 1000 - 100 + 1000 - 70 == 1830
    assert not df["amount"].isna().any()


def test_source_total_includes_stocks_dividends_and_sale_profit_but_never_premiums():
    inc = _src([[date(2025, 1, 5), "Salary", "s", 1000.0, 100.0, None]])
    div = pd.DataFrame({"date": [date(2025, 2, 1)], "amount": [40.0]})
    sold = pd.DataFrame({"date": [date(2025, 3, 1)], "pnl": [-25.0]})
    df = an.income_frame(inc, div, sold)
    assert df["amount"].sum() == 1000 - 100 + 40 - 25 == 915
    assert "Insurance Premium" not in set(df["source"])
    assert an.income_frame(inc, div, sold, include_auto=False)["amount"].sum() == 900


def test_summary_income_is_source_total_minus_premiums_paid():
    inc = _src([[date(2025, 1, 5), "Salary", "s", 1000.0, 100.0, None]])
    d, r = _empty_auto()
    prem = pd.DataFrame({"date": [date(2025, 1, 20), date(2025, 6, 20)], "amount": [60.0, 60.0]})
    source = an.income_frame(inc, d, r)["amount"].sum()
    summary = an.income_frame(inc, d, r, premiums=prem)["amount"].sum()
    assert source == 900 and summary == source - 120 == 780


def test_source_pivot_year_with_no_rows_is_zero_not_error():
    inc = _src([[date(2025, 1, 5), "Salary", "s", 1000.0, None, None]])
    d, r = _empty_auto()
    pv = _source_total_row(an.income_frame(inc, d, r), 2030)
    assert pv.loc["Total", "Total"] == 0


def test_negative_amounts_on_any_source_reduce_the_totals():
    inc = _src([[date(2025, 4, 1), "Dividend/FD", "FD closed early, penalty", -1500.0, None, None],
                [date(2025, 4, 2), "Dividend/FD", "FD interest", 4000.0, 100.0, None],
                [date(2025, 4, 3), "Salary", "reversal", -200.0, None, None]])
    d, r = _empty_auto()
    pv = _source_total_row(an.income_frame(inc, d, r), 2025)
    assert pv.loc["Dividend/FD", 4] == 2500 and pv.loc["Salary", 4] == -200
    assert pv.loc["Total", "Total"] == 2500 - 100 - 200 == 2200


def test_inflow_by_year_and_yoy_change():
    inc = _src([[date(2023, 3, 1), "Salary", "s", 1000.0, 200.0, None],
                [date(2024, 3, 1), "Salary", "s", 1500.0, 300.0, None],
                [date(2024, 9, 1), "Dividend/FD", "fd penalty", -100.0, None, None],
                [date(2026, 1, 1), "Salary", "s", 600.0, 0.0, None]])
    d, r = _empty_auto()
    y = an.inflow_by_year(an.income_frame(inc, d, r)).set_index("year")
    assert y.loc[2023, "gross"] == 1000 and y.loc[2023, "tax"] == 200 and y.loc[2023, "net"] == 800
    assert y.loc[2024, "gross"] == 1400 and y.loc[2024, "net"] == 1100
    assert pd.isna(y.loc[2023, "yoy_pct"])                       # first year has nothing to compare with
    assert round(y.loc[2024, "yoy_pct"], 6) == round((1100 - 800) / 800 * 100, 6) == 37.5
    assert y.loc[2025, "net"] == 0 and y.loc[2025, "yoy_pct"] == -100.0      # a missing year is shown as zero
    assert pd.isna(y.loc[2026, "yoy_pct"])                       # no % from a zero year
    assert an.inflow_by_year(an.income_frame(inc.iloc[0:0], d, r)).empty
    # growth of each series: gross 1000 -> 1400 (+40%), tax 200 -> 300 (+50%), after tax 800 -> 1100 (+37.5%)
    assert round(y.loc[2024, "gross_yoy_pct"], 6) == 40.0 and round(y.loc[2024, "tax_yoy_pct"], 6) == 50.0
    assert pd.isna(y.loc[2023, "gross_yoy_pct"]) and pd.isna(y.loc[2026, "tax_yoy_pct"])


def test_memo_row_shows_epf_nps_per_month_and_year_without_touching_totals():
    inc = _src([[date(2025, 1, 28), "Salary", "A", 1000.0, 100.0, 50.0], [date(2025, 1, 30), "Salary", "B", 500.0, None, 25.0],
                [date(2026, 3, 1), "Salary", "A", 700.0, 70.0, None]])
    m = an.memo_pivot(inc, 2025, "month")
    assert m[1] == 75 and m.sum() == 75 and len(m) == 12
    y = an.memo_pivot(inc, None, "year")
    assert y[2025] == 75 and y.get(2026, 0) == 0
    d, r = _empty_auto()
    assert _source_total_row(an.income_frame(inc, d, r), 2025).loc["Total", "Total"] == 1500 - 100


def test_rsu_value_is_units_times_buy_value_times_rate():
    v = an.rsu_value(10, 25.5, 83.2)
    assert v["usd"] == 255 and round(v["inr"], 2) == 21216.0
    assert an.rsu_value(0, 25.5, 83.2) == {"usd": 0.0, "inr": 0.0}
    assert an.rsu_value(None, None, None)["inr"] == 0.0


def test_average_growth_and_trend_line():
    net = pd.Series({2022: 100.0, 2023: 110.0, 2024: 132.0})
    yoy = pd.Series([float("nan"), 10.0, 20.0])
    assert an.average_growth(yoy) == 15.0
    assert an.average_growth(pd.Series([float("nan")])) is None
    t = an.growth_line(net, 15.0)
    assert t[2022] == 100 and round(t[2023], 6) == 115 and round(t[2024], 6) == 132.25
    # years before the first positive year are blank, and no average gives no line
    t2 = an.growth_line(pd.Series({2021: 0.0, 2022: 200.0, 2023: 100.0}), 10.0)
    assert pd.isna(t2[2021]) and t2[2022] == 200 and round(t2[2023], 6) == 220
    assert an.growth_line(net, None).isna().all()


def test_rsu_vested_is_an_income_source_counted_in_totals():
    inc = _src([[date(2025, 5, 1), "RSU Vested", "Intel RSU", 21216.0, 6000.0, None]])
    d, r = _empty_auto()
    pv = _source_total_row(an.income_frame(inc, d, r), 2025)
    assert pv.loc["RSU Vested", "Total"] == 21216 and pv.loc["Total", "Total"] == 21216 - 6000
    assert not val.validate("income", pd.DataFrame({"id": [1], "date": [date(2025, 5, 1)], "source": ["RSU Vested"],
                                                    "amount": [21216.0]}))


def test_unsold_rsus_are_valued_at_todays_rate_not_the_vest_day_rate():
    inc = _src([[date(2025, 5, 1), "RSU Vested", "Intel", 21216.0, 6000.0, None],      # 255 USD at 83.2 on the vest day
                [date(2025, 5, 2), "Salary", "pay", 1000.0, None, None]])
    inc["rsu_qty"], inc["rsu_buy_price"] = [10.0, None], [25.5, None]
    d, r = _empty_auto()
    now = an.income_frame(inc, d, r, usd_inr=90.0)
    assert now.loc[now["source"] == "RSU Vested", "amount"].iloc[0] == 255 * 90      # follows the live rate
    assert now["amount"].sum() == 255 * 90 + 1000 - 6000
    assert an.income_frame(inc, d, r)["amount"].sum() == 21216 + 1000 - 6000         # no live rate: the vest-day value
    inc2 = inc.copy(); inc2["rsu_qty"] = [None, None]
    assert an.income_frame(inc2, d, r, usd_inr=90.0)["amount"].sum() == 21216 + 1000 - 6000   # old rows keep their ₹ value


def test_rsu_table_values_the_kept_shares_at_todays_rate():
    inc = _src([[date(2025, 5, 1), "RSU Vested", "Intel", 21216.0, None, None]])
    inc["rsu_qty"], inc["rsu_buy_price"] = [10.0], [25.5]
    t = an.rsu_table(inc, 90.0)
    assert t["held"].iloc[0] == 10 and t["held_usd"].iloc[0] == 255 and t["held_inr_now"].iloc[0] == 22950
    assert pd.isna(an.rsu_table(inc, None)["held_inr_now"].iloc[0])
    assert an.rsu_table(inc.assign(rsu_qty=[None]), 90.0).empty


def test_rsu_total_is_qty_times_buy_price_times_todays_rate():
    inc = _src([[date(2026, 11, 1), "RSU Vested", "Intel", 0.0, None, None]])
    inc["rsu_qty"], inc["rsu_buy_price"] = [7.0], [44.65]
    d, r = _empty_auto()
    df = an.income_frame(inc, d, r, usd_inr=93.92)
    assert round(df["amount"].sum(), 2) == round(7 * 44.65 * 93.92, 2) == 29354.7      # 312.55 USD x 93.92
    assert round(an.rsu_table(inc, 93.92)["held_usd"].iloc[0], 2) == 312.55


def test_rsu_sold_for_tax_is_fixed_at_the_sell_date_and_kept_shares_follow_today():
    # 10 vested at $50. 3 sold for tax at $52 when USD/INR was 80. Today USD/INR is 90.
    inc = _src([[date(2026, 5, 1), "RSU Vested", "Intel", 0.0, None, None]])
    inc["rsu_qty"], inc["rsu_buy_price"] = [10.0], [50.0]
    inc["rsu_sold_qty"], inc["rsu_sell_price"] = [3.0], [52.0]
    inc["rsu_sell_date"], inc["rsu_sell_fx"] = [date(2026, 5, 2)], [80.0]
    d, r = _empty_auto()
    df = an.income_frame(inc, d, r, usd_inr=90.0)
    gross = 7 * 50 * 90 + 3 * 50 * 80                      # kept shares live, sold shares at the sell-date rate
    tax = 3 * 52 * 80                                      # 12,480 paid by selling shares
    assert df.loc[df["source"] == "RSU Vested", "amount"].iloc[0] == gross == 31500 + 12000
    assert -df.loc[df["source"] == "Taxes Paid", "amount"].sum() == tax
    assert df["amount"].sum() == gross - tax
    assert df.loc[df["source"] == "Taxes Paid", "date"].iloc[0] == date(2026, 5, 2)    # tax lands on the sell date
    # a different live rate only moves the kept shares
    assert an.income_frame(inc, d, r, usd_inr=100.0)["amount"].sum() == 7 * 50 * 100 + 12000 - tax
    t = an.rsu_table(inc, 90.0).iloc[0]
    assert t["held"] == 7 and t["tax_inr"] == tax and t["held_inr_now"] == 7 * 50 * 90


def test_rsu_selling_more_than_vested_is_capped_and_flagged():
    inc = _src([[date(2026, 5, 1), "RSU Vested", "Intel", 0.0, None, None]])
    inc["rsu_qty"], inc["rsu_buy_price"], inc["rsu_sold_qty"] = [2.0], [10.0], [5.0]
    inc["rsu_sell_price"], inc["rsu_sell_date"], inc["rsu_sell_fx"] = [10.0], [None], [80.0]
    d, r = _empty_auto()
    assert -an.income_frame(inc, d, r, usd_inr=90.0).query("source == 'Taxes Paid'")["amount"].sum() == 2 * 10 * 80
    probs = val.validate("income", inc.assign(id=1, amount=100.0))
    assert any("cannot be more than the shares vested" in x for x in probs)


def test_fx_history_picks_the_last_close_on_or_before_the_day():
    from datetime import datetime, timezone
    import market
    ts = lambda y, m, d: int(datetime(y, m, d, tzinfo=timezone.utc).timestamp())
    payload = {"chart": {"result": [{"timestamp": [ts(2026, 5, 1), ts(2026, 5, 4), ts(2026, 5, 5)],
                                     "indicators": {"quote": [{"close": [83.0, None, 84.5]}]}}]}}
    assert market.parse_fx_history(payload, date(2026, 5, 2)) == 83.0       # a weekend: the Friday close
    assert market.parse_fx_history(payload, date(2026, 5, 5)) == 84.5
    assert market.parse_fx_history(payload, date(2026, 4, 1)) is None
    assert market.parse_fx_history({}, date(2026, 5, 5)) is None


def test_extra_tax_is_a_percentage_of_fd_dividend_pl_and_misc_subtracted_from_the_total():
    inc = _src([[date(2025, 1, 5), "Salary", "pay", 1000.0, 100.0, None],
                [date(2025, 1, 6), "Dividend/FD", "fd", 400.0, None, None],
                [date(2025, 1, 7), "PL Booking", "swing", 100.0, None, None],
                [date(2025, 2, 7), "Miscellaneous", "gift", 500.0, None, None],
                [date(2026, 2, 7), "PL Booking", "loss", -200.0, None, None]])
    d, r = _empty_auto()
    df = an.income_frame(inc, d, r, extra_tax_pct=30.0)
    base = 400 + 100 + 500 - 200                                  # Salary is not part of the base
    assert round(-df.loc[df["source"] == "Extra tax to be paid", "amount"].sum(), 6) == 0.3 * base == 240
    assert round(df["amount"].sum(), 6) == 1000 - 100 + base - 240
    m25 = _source_total_row(df, 2025)
    assert round(m25.loc["Extra tax to be paid", 1], 6) == -150 and round(m25.loc["Extra tax to be paid", 2], 6) == -150
    y = _source_total_row(df, None, by="year")                    # month and year views agree
    assert round(y.loc["Extra tax to be paid", "Total"], 6) == -240
    assert round(y.loc["Total", "Total"], 6) == round(df["amount"].sum(), 6)
    assert "Extra tax to be paid" not in set(an.income_frame(inc, d, r)["source"])        # 0% adds nothing
    yo = an.inflow_by_year(df).set_index("year")
    assert round(yo.loc[2025, "tax"], 6) == 100 + 300             # tax paid + extra tax count as tax in the chart


def test_extra_tax_per_row_percentage_and_the_tax_to_be_paid_column():
    inc = _src([[date(2025, 1, 1), "Dividend/FD", "no tax yet", 1000.0, None, None],      # default % applies
                [date(2025, 1, 2), "Dividend/FD", "already taxed", 1000.0, 100.0, None],  # tax entered: default skipped
                [date(2025, 1, 3), "PL Booking", "own %", 2000.0, None, None],            # its own 10%
                [date(2025, 1, 4), "Dividend/FD", "taxed and own %", 1000.0, 100.0, None],  # own % always applies
                [date(2025, 1, 5), "Salary", "pay", 5000.0, None, None]])                 # never carries extra tax
    inc["tax_pct"] = [None, None, 10.0, 5.0, 25.0]
    due = an.extra_tax_rows(inc, 30.0)
    assert list(due.round(6)) == [300.0, 0.0, 200.0, 50.0, 0.0]
    d, r = _empty_auto()
    df = an.income_frame(inc, d, r, extra_tax_pct=30.0)
    assert round(-df.loc[df["source"] == "Extra tax to be paid", "amount"].sum(), 6) == 550
    assert round(df["amount"].sum(), 6) == (1000 + 1000 + 2000 + 1000 + 5000) - 200 - 550
    assert an.extra_tax_rows(inc, 0.0).tolist() == [0.0, 0.0, 200.0, 50.0, 0.0]            # no default: only typed %s
    assert val.validate("income", inc.assign(id=1, tax_pct=[None, None, None, 120.0, None]))


def test_default_extra_tax_also_applies_to_stock_dividends_and_sale_profit():
    inc = _src([[date(2025, 1, 5), "Salary", "pay", 1000.0, None, None]])
    div = pd.DataFrame({"date": [date(2025, 2, 1)], "amount": [100.0]})
    sold = pd.DataFrame({"date": [date(2025, 3, 1)], "pnl": [200.0]})
    df = an.income_frame(inc, div, sold, extra_tax_pct=10.0)
    assert round(-df.loc[df["source"] == "Extra tax to be paid", "amount"].sum(), 6) == 30
