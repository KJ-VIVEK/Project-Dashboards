"""Seed ~14 months of demo data:  python seed.py [--reset] [--user NAME]"""
import os
import random
import sys
from datetime import date, timedelta

import db

random.seed(7)


def main(reset=False, user="Demo"):
    path = db.user_path(user)
    if reset and os.path.exists(path):
        os.remove(path)
    if not os.path.exists(path):
        db.create_user(user)
    db.set_active(path)
    today = date.today()
    start = (today.replace(day=1) - timedelta(days=30 * 13)).replace(day=1)

    fixed = [("Gym", "Gym", 1500, "monthly"), ("Netflix", "Subscriptions", 649, "monthly"),
             ("Protein", "Protein", 3200, "monthly"), ("Fuel", "Fuel", 4000, "monthly"),
             ("Home loan EMI", "EMI", 18000, "monthly"), ("Index fund SIP", "SIP", 10000, "monthly")]
    for n, c, a, f in fixed:
        db.add_fixed_item(n, c, a, f, start.replace(day=5))

    # inflow: bi-weekly salary, bonus now and then
    d = start
    while d <= today:
        db.add_entry(d, "inflow", "Salary", 32000)
        if d.month in (3, 10) and d.day <= 14:
            db.add_entry(d, "inflow", "Bonus", 15000)
        d += timedelta(days=14)

    # variable spend: 3-4 items per fortnight; December/May trips, and a spike this month
    d = start
    cats = {"Dining": (400, 1500), "Groceries": (800, 2500), "Transport": (200, 900),
            "Shopping": (500, 3000), "Entertainment": (300, 1200)}
    while d <= today:
        for _ in range(random.randint(3, 5)):
            c = random.choice(list(cats))
            db.add_entry(d + timedelta(days=random.randint(0, 13)), "variable", c,
                         random.randint(*cats[c]), tag="habit")
        if d.month in (12, 5) and d.day <= 14:
            db.add_entry(d, "variable", "Travel", random.randint(9000, 15000), "Trip", "unplanned")
        d += timedelta(days=14)
    db.add_entry(today.replace(day=1), "variable", "Dining", 9000, "Impromptu party", "impromptu")

    db.post_due_fixed()
    print("Seeded", len(db.get_entries()), "entries for", user)


if __name__ == "__main__":
    name = sys.argv[sys.argv.index("--user") + 1] if "--user" in sys.argv else "Demo"
    main("--reset" in sys.argv, name)
