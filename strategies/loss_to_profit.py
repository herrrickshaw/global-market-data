#!/usr/bin/env python3
# Loss to Profit Turnaround — previously loss-making companies that just posted a
# profitable quarter (sign flip in quarterly net income).
from __future__ import annotations

from .base import Result, StockData, safe

META = {
    "name": "Loss to Profit Turnaround",
    "slug": "loss_to_profit",
    "category": "fundamental",
    "description": "Previously loss-making companies reporting a turnaround to "
    "profit in the latest quarter.",
    "needs": "fundamentals",
}


def screen(s: StockData) -> Result | None:
    g = s.f
    q = g("quarterly_net_income") or []  # newest-first list
    vals = [safe(x) for x in q if safe(x) is not None]
    if len(vals) < 2:
        return None
    latest = vals[0]
    prior = vals[1:4]  # up to 3 preceding quarters
    was_loss = any(v is not None and v < 0 for v in prior)
    now_profit = latest is not None and latest > 0
    passed = was_loss and now_profit
    swing = None
    if prior and prior[0] is not None and prior[0] != 0:
        swing = (latest - prior[0]) / abs(prior[0]) * 100
    return Result(
        s.symbol,
        META["slug"],
        passed=passed,
        score=round(latest, 2) if latest is not None else None,
        metrics={
            "LatestQ_NI": latest,
            "PriorQ_NI": prior[0] if prior else None,
            "WasLoss": int(was_loss),
            "NowProfit": int(now_profit),
            "Swing%": round(swing, 1) if swing is not None else None,
        },
        note="turnaround" if passed else "",
    )
