import os
import tempfile

import pytest

import db
import market


@pytest.fixture()
def path():
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "t.db")
        db.init_db(p)
        yield p


def test_parsers():
    ok = {"chart": {"result": [{"meta": {"regularMarketPrice": 123.4, "currency": "INR"}}]}}
    assert market.parse_yahoo(ok) == (123.4, "INR")
    assert market.parse_yahoo({"chart": {"result": None}}) is None
    assert market.parse_yahoo({}) is None
    assert market.parse_mf({"data": [{"date": "x", "nav": "55.5"}]}) == (55.5, "INR")
    assert market.parse_mf({"data": []}) is None


def test_convert():
    assert market.convert(10, "USD", "INR", 80) == 800
    assert market.convert(800, "INR", "USD", 80) == 10
    assert market.convert(5, "INR", "INR", 0) == 5
    assert market.convert(5, "USD", "INR", 0) is None
    assert market.convert(5, "GBP", "INR", 80) is None


def fake(monkeypatch, quotes, fx=80.0, metals=None):
    monkeypatch.setattr(market, "quote", lambda s: quotes.get(s))
    monkeypatch.setattr(market, "fetch_usd_inr", lambda: fx)
    monkeypatch.setattr(market, "fetch_metals_inr_per_g", lambda f: metals or {})
    monkeypatch.delenv("ASSET_OFFLINE", raising=False)


def test_refresh_updates_prices_fx_and_converts(path, monkeypatch):
    db.insert("securities", {"name": "Reliance", "symbol": "RELIANCE.NS", "category": "India - High Quality",
                             "currency": "INR"}, path)
    db.insert("securities", {"name": "Apple", "symbol": "AAPL", "category": "International - Stable",
                             "currency": "USD"}, path)
    db.insert("securities", {"name": "Bad", "symbol": "NOPE", "category": "ETFs", "currency": "INR"}, path)
    db.insert("securities", {"name": "Manual", "category": "Unlisted Stocks", "current_price": 7}, path)
    fake(monkeypatch, {"RELIANCE.NS": (2900.0, "INR"), "AAPL": (200.0, "USD")})
    r = market.refresh(path, force=True)
    s = db.get_table("securities", path).set_index("name")
    assert s.loc["Reliance", "current_price"] == 2900 and s.loc["Apple", "current_price"] == 200  # native currency
    assert s.loc["Manual", "current_price"] == 7                                                    # untouched
    assert r["failed"] == ["Bad"] and r["changed"] == 2 and r["fx"] == 80.0
    assert db.usd_inr(path) == 80.0


def test_usd_quote_for_inr_holding_is_converted(path, monkeypatch):
    db.insert("securities", {"name": "BTC", "symbol": "BTC-USD", "category": "Crypto", "currency": "INR"}, path)
    fake(monkeypatch, {"BTC-USD": (100.0, "USD")}, fx=80.0)
    market.refresh(path, force=True)
    assert db.get_table("securities", path).iloc[0]["current_price"] == 8000


def test_refresh_is_throttled_offline_aware_and_failure_safe(path, monkeypatch):
    db.insert("securities", {"name": "A", "symbol": "A", "category": "ETFs", "current_price": 5}, path)
    fake(monkeypatch, {"A": (9.0, "INR")})
    assert "changed" in market.refresh(path)
    assert market.refresh(path) == {"skipped": "fresh"}
    monkeypatch.setenv("ASSET_OFFLINE", "1")
    assert market.refresh(path, force=True) == {"skipped": "offline"}
    monkeypatch.delenv("ASSET_OFFLINE")
    fake(monkeypatch, {}, fx=None)                      # everything fails: last price is kept
    r = market.refresh(path, force=True)
    assert r["failed"] == ["A"] and db.get_table("securities", path).iloc[0]["current_price"] == 9


def test_metals_follow_live_price_with_premium_unless_switched_off(path, monkeypatch):
    fake(monkeypatch, {}, metals={"Gold": 7000.0, "Silver": 90.0})
    db.meta_set("premium_gold", 10, path)
    market.refresh(path, force=True)
    assert db.metal_prices(path) == {"Gold": 7700.0, "Silver": 90.0}
    db.meta_set("metal_auto", "0", path)
    fake(monkeypatch, {}, metals={"Gold": 1.0})
    market.refresh(path, force=True)
    assert db.metal_prices(path)["Gold"] == 7700.0
