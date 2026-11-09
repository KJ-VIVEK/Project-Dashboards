"""Live market data: quotes, USD/INR and metal prices (free public endpoints, no API keys).

Everything degrades gracefully: on any network/parse failure a quote is simply skipped and the last stored
price stays in force. Set ASSET_OFFLINE=1 to disable all network calls.

Symbol conventions (the Symbol field of a holding):
  RELIANCE.NS, TCS.BO   Indian stocks/ETFs (Yahoo Finance)      AAPL, TSLA   US stocks
  BTC-INR, BTC-USD      crypto                                  119551       AMFI scheme code -> mutual fund NAV
"""
from __future__ import annotations

import json
import os
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import db

UA = {"User-Agent": "Mozilla/5.0 (compatible; AssetTracker/1.0)"}
OZ_GRAMS = 31.1035
TIMEOUT = 8


def offline() -> bool:
    return os.environ.get("ASSET_OFFLINE") == "1"


def _get_json(url: str):
    req = urllib.request.Request(url, headers=UA)
    with urllib.request.urlopen(req, timeout=TIMEOUT) as r:  # noqa: S310 (fixed https endpoints)
        return json.load(r)


def parse_yahoo(payload) -> tuple[float, str] | None:
    try:
        meta = payload["chart"]["result"][0]["meta"]
        price = float(meta["regularMarketPrice"])
        return (price, str(meta.get("currency", ""))) if price > 0 else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def yahoo_quote(symbol: str):
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(symbol)}?range=1d&interval=1d"
    try:
        return parse_yahoo(_get_json(url))
    except Exception:
        return None


def parse_mf(payload) -> tuple[float, str] | None:
    try:
        nav = float(payload["data"][0]["nav"])
        return (nav, "INR") if nav > 0 else None
    except (KeyError, IndexError, TypeError, ValueError):
        return None


def mf_nav(code: str):
    try:
        return parse_mf(_get_json(f"https://api.mfapi.in/mf/{urllib.parse.quote(code)}"))
    except Exception:
        return None


def quote(symbol: str):
    """(price, currency) or None."""
    symbol = (symbol or "").strip()
    if not symbol:
        return None
    return mf_nav(symbol) if symbol.isdigit() else yahoo_quote(symbol)


def fetch_usd_inr() -> float | None:
    q = yahoo_quote("USDINR=X")
    if q:
        return q[0]
    try:  # fallback source
        rate = float(_get_json("https://open.er-api.com/v6/latest/USD")["rates"]["INR"])
        return rate if rate > 0 else None
    except Exception:
        return None


def convert(price: float, src: str, dst: str, fx: float) -> float | None:
    """Convert a quote between INR and USD; None when the pair is unsupported or the rate unknown."""
    src, dst = (src or dst).upper(), dst.upper()
    if src == dst:
        return price
    if not fx:
        return None
    if (src, dst) == ("USD", "INR"):
        return price * fx
    if (src, dst) == ("INR", "USD"):
        return price / fx
    return None


def fetch_metals_inr_per_g(fx: float) -> dict[str, float]:
    """Spot gold/silver (futures, USD/oz) converted to ₹/g. International price: no import duty or GST."""
    out = {}
    for metal, sym in (("Gold", "GC=F"), ("Silver", "SI=F")):
        q = yahoo_quote(sym)
        if q and fx:
            out[metal] = q[0] * fx / OZ_GRAMS
    return out


def refresh(path: str | None = None, force: bool = False, max_age: int = 60) -> dict:
    """Fetch FX, holding prices and (optionally) metal prices, and store them.

    Returns {"skipped": reason} or {"changed": n, "failed": [names], "fx": rate|None, "at": iso time}.
    Throttled by `max_age` seconds unless `force`.
    """
    if offline():
        return {"skipped": "offline"}
    now = datetime.now()
    last = db.meta_get("last_refresh", None, path)
    if not force and last and (now - datetime.fromisoformat(last)).total_seconds() < max_age:
        return {"skipped": "fresh"}
    db.meta_set("last_refresh", now.isoformat(timespec="seconds"), path)

    result = {"changed": 0, "failed": [], "fx": None, "at": now.strftime("%H:%M:%S")}
    live_fx = fetch_fx_safe()
    if live_fx:
        db.meta_set("fx_usdinr", live_fx, path)
        db.meta_set("fx_time", now.isoformat(timespec="seconds"), path)
    fx = db.usd_inr(path)
    result["fx"] = fx or None

    secs = db.get_table("securities", path)
    secs = secs[secs["symbol"].fillna("").str.strip() != ""]
    with ThreadPoolExecutor(max_workers=8) as ex:
        quotes = list(ex.map(quote, secs["symbol"]))
    for (_, s), q in zip(secs.iterrows(), quotes):
        native = "USD" if str(s["currency"]).strip().upper() == "USD" else "INR"
        price = convert(q[0], q[1], native, fx) if q else None
        if price is None:
            result["failed"].append(s["name"])
            continue
        old = s["current_price"]
        if old is None or old != old or abs(price - old) > 1e-9:  # NaN-safe
            result["changed"] += 1
        db.update_row("securities", s["id"], {"current_price": round(price, 4), "price_date": now.date()}, path)

    if db.meta_get("metal_auto", "1", path) == "1" and fx:
        for metal, per_g in fetch_metals_inr_per_g(fx).items():
            premium = float(db.meta_get(f"premium_{metal.lower()}", 0, path) or 0)
            new = round(per_g * (1 + premium / 100), 2)
            if abs(new - float(db.meta_get(f"price_{metal.lower()}", 0, path) or 0)) > 1e-9:
                result["changed"] += 1
            db.meta_set(f"price_{metal.lower()}", new, path)
    return result


def fetch_fx_safe():
    try:
        return fetch_usd_inr()
    except Exception:
        return None
