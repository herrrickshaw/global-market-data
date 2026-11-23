"""quick_reference is a static strategy card: importing it must define every card as non-empty text."""

import quick_reference as qr


def test_cards_are_defined_text():
    cards = {n: v for n, v in vars(qr).items() if n.isupper() and isinstance(v, str)}
    assert {
        "KEY_METRICS",
        "PROFIT_SCENARIOS",
        "COST_BREAKDOWN",
        "API_COMPARISON",
        "INDICATORS_REFERENCE",
        "EXECUTION_CHECKLIST",
    } <= set(cards)
    assert all(v.strip() for v in cards.values())
    assert "₹3,00,000" in qr.KEY_METRICS and "Zerodha" in qr.API_COMPARISON
