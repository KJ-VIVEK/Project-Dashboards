"""Light-hearted one-liners that sit next to the real analysis (never instead of it).

Picks are deterministic per (kind, seed) so a caption doesn't flicker on every rerun.
"""
from __future__ import annotations

import random

_LINES = {
    "save_great": ["Future-you is sending a thank-you card.", "Look at you, adulting at expert level.",
                   "Your wallet is doing push-ups."],
    "save_ok": ["Solid. Not yacht money, but solid.", "Steady like a Sunday morning.",
                "Respectable. Your piggy bank nods."],
    "save_low": ["Savings are playing hide-and-seek.", "Your budget called; it wants a word.",
                 "Thin margins, but the plot is still alive."],
    "save_neg": ["Spending outran earning. Marathon, not a sprint, friend.",
                 "The wallet is on a diet it did not sign up for.",
                 "Red month. Deep breath, then trim the variable stuff."],
    "fixed_high": ["Your bills have moved in and put up posters.",
                   "Commitments are eating the buffet before you arrive."],
    "fixed_ok": ["Fixed costs are well-behaved house guests."],
    "var_spike": ["Somebody said 'one quick dinner' and meant five.", "Impulse called. You answered. Twice.",
                  "That's a plot twist your budget didn't read."],
    "neg_flow": ["Outflow is sprinting; inflow is still tying its shoelaces."],
    "var_share": ["Spontaneity is great. It's also invoiced."],
    "rising": ["The trend line is climbing like it has a gym membership (unlike you, maybe)."],
    "budget_over": ["Budget? More like a gentle suggestion this month.", "Over budget — bold strategy."],
    "budget_near": ["Almost there — the budget is clutching its pearls."],
    "budget_ok": ["Comfortably within limits. Smug mode: on."],
    "upcoming_none": ["No bills on the horizon. Enjoy the silence."],
    "upcoming": ["Bills are coming. Not a drill.", "Heads up: money is about to leave the building.",
                 "Your subscriptions are about to say hello."],
    "forecast_pos": ["The crystal ball says: surplus. Don't tell the shopping cart."],
    "forecast_neg": ["The crystal ball says: tight. It's judging quietly."],
    "empty": ["It's quiet in here. Too quiet. Add something."],
    "fund_low": ["Under 3 months of cushion: a fragile rainy-day umbrella."],
    "fund_ok": ["3–6 months: a decent umbrella."],
    "fund_great": ["6+ months: bunker-grade umbrella."],
    "tags": ["'Impromptu' is a lifestyle, apparently."],
}


def pick(kind: str, seed: str = "") -> str:
    lines = _LINES[kind]
    return random.Random(f"{kind}-{seed}").choice(lines)


def savings(rate: float | None, seed: str = "") -> str:
    if rate is None:
        return pick("empty", seed)
    if rate < 0:
        return pick("save_neg", seed)
    if rate < 0.1:
        return pick("save_low", seed)
    return pick("save_great" if rate >= 0.3 else "save_ok", seed)


def alert(title: str, seed: str = "") -> str:
    if title.startswith("High"):
        return pick("var_spike", seed)
    return {"Negative cash flow": pick("neg_flow", seed),
            "Heavy fixed commitments": pick("fixed_high", seed),
            "Variable share rising": pick("var_share", seed)}.get(title, pick("rising", seed))


def budget(pct: float, seed: str = "") -> str:
    return pick("budget_over" if pct > 1 else "budget_near" if pct > 0.8 else "budget_ok", seed)


def fund(months: float, seed: str = "") -> str:
    return pick("fund_low" if months < 3 else "fund_ok" if months < 6 else "fund_great", seed)
