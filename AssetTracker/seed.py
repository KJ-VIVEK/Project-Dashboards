"""Generate a fake demo database (never touches your real data).

    python seed.py                 # writes data/demo.db
    python seed.py --out x.db --reset
    ASSET_DB=data/demo.db streamlit run app.py
"""
import os
import random
import sys
from datetime import date, timedelta

import db

random.seed(11)


def main(out, reset):
    if os.path.exists(out):
        if not reset:
            sys.exit(f"{out} exists; pass --reset to overwrite it (demo files only!)")
        os.remove(out)
    db.init_db(out)
    ins = lambda t, r: db.insert(t, r, out)  # noqa: E731
    today = date.today()
    first = (today.replace(day=1) - timedelta(days=330)).replace(day=1)

    d = first
    while d <= today:
        ins("income", {"date": d.replace(day=28) if d.month != 2 else d.replace(day=27), "source": "Salary",
                       "description": "Acme Corp salary", "amount": 400000})
        if d.month in (3, 9):
            ins("income", {"date": d.replace(day=15), "source": "Dividend/FD", "description": "FD interest",
                           "amount": random.randint(6000, 9000)})
        if d.month % 3 == 0:
            ins("income", {"date": d.replace(day=10), "source": "PL Booking", "description": "Swing trades",
                           "amount": random.randint(-5000, 20000)})
        d = (d + timedelta(days=32)).replace(day=1)
    ins("income", {"date": today - timedelta(days=40), "source": "Miscellaneous", "description": "Freelance gig",
                   "amount": 18000})

    secs = [("Reliance Industries", "India - High Quality", "Diversified, strong cash flows", 100, 2400, 2900),
            ("HDFC Bank", "India - High Quality", "Core banking compounder", 150, 1500, 1650),
            ("Small-cap Co", "India - Risky", "Turnaround bet; size small", 400, 80, 65),
            ("Nifty 50 Index Fund", "Index Funds", "Core, low cost", 900, 140, 168),
            ("Parag Parikh Flexi Cap", "Mutual Funds", "Global diversification", 700, 60, 78),
            ("Gold BeES", "ETFs", "Liquid gold exposure", 500, 50, 62),
            ("Apple Inc", "International - Stable", "Cash machine", 20, 130, 190),
            ("Tesla", "International - Risky", "High-conviction speculation", 5, 220, 250),
            ("Embassy REIT", "REITs/InvITs", "Rental income", 300, 350, 385),
            ("Bitcoin", "Crypto", "Small asymmetric bet", 0.05, 4500000, 7400000)]
    for name, cat, why, qty, buy, now in secs:
        usd = cat.startswith("International")
        sym = {"Apple Inc": "AAPL", "Tesla": "TSLA", "Reliance Industries": "RELIANCE.NS",
               "HDFC Bank": "HDFCBANK.NS", "Bitcoin": "BTC-INR"}.get(name, "")
        sid = ins("securities", {"name": name, "symbol": sym, "category": cat, "reason": why, "current_price": now,
                                 "price_date": today, "currency": "USD" if usd else "INR"})
        half = qty / 2
        ins("trades", {"security_id": sid, "date": today - timedelta(days=300), "side": "Buy", "qty": half,
                       "price": buy * 0.95, "fees": 40, "fx_rate": 83.0 if usd else None})
        ins("trades", {"security_id": sid, "date": today - timedelta(days=120), "side": "Buy", "qty": half,
                       "price": buy * 1.05, "fees": 40, "fx_rate": 86.0 if usd else None})
        if cat in ("India - High Quality", "REITs/InvITs", "International - Stable"):
            for k in (200, 100):
                ins("dividends", {"security_id": sid, "date": today - timedelta(days=k),
                                  "amount": round(qty * buy * 0.01, 2)})
    ins("securities", {"name": "Unlisted Startup", "category": "Unlisted Stocks", "reason": "Friend's startup",
                       "current_price": 1200})
    ins("trades", {"security_id": 11, "date": today - timedelta(days=500), "side": "Buy", "qty": 100, "price": 1000})
    ins("trades", {"security_id": 4, "date": today - timedelta(days=30), "side": "Sell", "qty": 100, "price": 160})

    for kind, name, amt, purpose, rate, mat, tgt in [
            ("Savings Account", "HDFC Savings", 240000, "Day-to-day", 3.0, None, None),
            ("Cash in Hand", "Wallet", 8000, "", None, None, None),
            ("FD", "SBI FD 1", 500000, "Emergency fund", 7.1, today + timedelta(days=400), None),
            ("FD", "ICICI FD 2", 300000, "House down payment", 7.3, today + timedelta(days=900), None),
            ("EPF", "EPF", 820000, "Retirement", 8.25, None, None),
            ("NPS", "NPS Tier 1", 310000, "Retirement", None, None, None),
            ("Short-term Goal", "Honeymoon fund", 65000, "Trip", None, today + timedelta(days=150), 200000)]:
        ins("savings", {"kind": kind, "name": name, "amount": amt, "purpose": purpose, "interest_rate": rate,
                        "maturity_date": mat, "target": tgt})

    ins("insurance", {"name": "Term Plan", "type": "Term Life", "provider": "HDFC Life", "policy_no": "TL-0001",
                      "sum_assured": 20000000, "premium": 18000, "frequency": "Yearly",
                      "next_due": today + timedelta(days=20), "link": "https://example.com/term.pdf", "active": True,
                      "start_date": date(2022, 4, 1)})
    ins("insurance_revisions", {"insurance_id": 1, "effective_from": date(today.year, 4, 1), "premium": 15000,
                                "note": "Tax change"})
    ins("insurance", {"name": "Family Floater", "type": "Health", "provider": "Star Health", "sum_assured": 1000000,
                      "premium": 1800, "frequency": "Monthly", "link": "https://example.com/health", "active": True,
                      "start_date": date(today.year, 7, 1)})

    # vehicle insurance: Own Damage renewed every year + a 5-year Third Party cover, both under one policy
    veh = ins("insurance", {"name": "Bike policy", "type": "Vehicle", "provider": "ICICI Lombard", "policy_no": "VH-77",
                            "sum_assured": 180000, "premium": 0, "frequency": "Yearly", "active": True})
    od1 = today - timedelta(days=500)
    od2 = today - timedelta(days=135)
    for label, prem, f_, s_, e_ in [
            ("Own Damage (OD)", 3400, "One-time (whole period)", od1, od1 + timedelta(days=364)),
            ("Own Damage (OD)", 3150, "One-time (whole period)", od2, od2 + timedelta(days=364)),
            ("Third Party (TP)", 5286, "One-time (whole period)", od1, date(od1.year + 5, od1.month, od1.day) - timedelta(days=1))]:
        ins("insurance", {"name": f"Bike policy · {label}", "type": "Vehicle", "provider": "ICICI Lombard",
                           "policy_no": "VH-77", "premium": prem, "frequency": f_, "start_date": s_, "end_date": e_,
                           "active": True, "parent_id": veh, "component": label})

    ins("loans", {"name": "Home loan", "lender": "SBI", "category": "Good", "principal": 4500000,
                  "outstanding": 3900000, "interest_rate": 8.5, "emi": 39000, "start_date": date(2023, 4, 1),
                  "end_date": date(2043, 4, 1), "details": "Co-borrower: spouse. Prepayment allowed without penalty."})
    ins("loans", {"name": "Car loan", "lender": "ICICI", "category": "Bad", "principal": 800000,
                  "outstanding": 450000, "interest_rate": 9.2, "emi": 17000, "end_date": date(2028, 6, 1)})
    ins("loans", {"name": "Credit card dues", "lender": "Axis", "category": "Worst", "outstanding": 30000,
                  "interest_rate": 42.0})
    ins("loans", {"name": "Personal loan (prepaid)", "lender": "HDFC", "category": "Worst", "principal": 200000,
                  "outstanding": 0, "interest_rate": 14.5, "start_date": "2024-02-01", "end_date": "2026-02-01", "status": "Closed",
                  "closed_date": "2024-11-15", "down_payment": 0, "other_charges": 3500, "installments_paid": 9,
                  "emi_paid": 86800, "emi_interest": 16300, "closing_payment": 133000})

    for metal, form, desc, g, rate, mk, gst in [("Gold", "Coin", "10 g coin (24K)", 10, 6200, 300, 1900),
                                                ("Gold", "Jewellery", "5 g chain (22K)", 5, 6900, 200, 1070),
                                                ("Silver", "Bar", "100 g bar", 100, 85, 150, 280)]:
        ins("metals", {"metal": metal, "form": form, "description": desc, "date": today - timedelta(days=400),
                       "grams": g, "rate_per_g": rate, "making_charges": mk, "gst": gst,
                       "purity": "22K" if "22K" in desc else ("999" if metal == "Silver" else "24K")})
    db.meta_set("price_gold", 7600, out)
    db.meta_set("price_silver", 98, out)
    db.meta_set("fx_usdinr", 88.0, out)

    ins("real_estate", {"name": "2BHK Whitefield", "kind": "Flat", "location": "Bengaluru",
                        "purchase_date": date(2023, 4, 1), "cost": 6000000, "area_sqft": 1100,
                        "registration": 420000, "brokerage": 60000, "other_charges": 90000, "rent_yielding": True,
                        "monthly_rent": 24000, "current_per_sqft": 6300})

    ins("notes", {"title": "Rebalance in March", "body": "Trim risky stocks back to target.", "kind": "Todo",
                  "done": False, "created": str(today), "updated": str(today)})
    ins("notes", {"title": "Idea: sovereign gold bonds", "body": "Check next tranche.", "kind": "Idea",
                  "done": False, "created": str(today), "updated": str(today)})
    db.set_target_pcts({"India - High Quality": 8, "India - Risky": 1, "Index Funds": 4, "Mutual Funds": 3, "ETFs": 1,
                        "International - Stable": 4, "International - Risky": 1, "REITs/InvITs": 2, "Crypto": 2,
                        "Gold": 3, "Silver": 0.5, "Real Estate": 10, "Cash & Savings": 15}, out)
    print("Demo database written to", out)


if __name__ == "__main__":
    out = sys.argv[sys.argv.index("--out") + 1] if "--out" in sys.argv else os.path.join(db.BASE_DIR, "data", "demo.db")
    main(out, "--reset" in sys.argv)
