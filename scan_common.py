"""Helpers shared by the full_*_market_scan.py scanners (trend label, Excel report writer)."""

from collections.abc import Callable, Sequence

import pandas as pd

TREND_BAND_PCT = 5.0  # +/- band around the 200-day MA that counts as "consolidation"


def trend_label(dist_ma, empty_label: str) -> str:
    """Label the % distance to the 200-day MA; ``empty_label`` is used when it is missing/zero."""
    if dist_ma and dist_ma > TREND_BAND_PCT:
        return "Above 200MA (Uptrend)"
    if dist_ma and dist_ma < -TREND_BAND_PCT:
        return "Below 200MA (Downtrend)"
    return "Near 200MA (Consolidation)" if dist_ma else empty_label


def write_scan_workbook(
    path,
    sheets: Sequence[tuple[list[dict], str, str | None]],
    style_fn: Callable | None = None,
) -> None:
    """Write ``(rows, sheet_name, sort_col)`` tuples to an .xlsx; sort descending; style if given."""
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        for rows, name, sort_col in sheets:
            if not rows:
                pd.DataFrame().to_excel(writer, sheet_name=name, index=False)
                continue
            df = pd.DataFrame(rows)
            if sort_col and sort_col in df.columns:
                df = df.sort_values(sort_col, ascending=False)
            df.to_excel(writer, sheet_name=name, index=False)
            if style_fn is not None:
                style_fn(writer.sheets[name])


GOLDEN_OR_ABOVE = ("GOLDEN_CROSS", "ABOVE_200DMA")
_SCREEN_FLAGS = (
    ("piotroski", "f_strong"),
    ("cc", "qualifies_cc"),
    ("mf", "qualifies_mf"),
    ("bc", "qualifies_bc"),
)


def ohlc_counts(all_rows: list[dict]) -> dict:
    """Headline Darvas / Golden-Cross counts over the OHLC rows."""
    return {
        "breakout": sum(1 for r in all_rows if r["Darvas_Signal"] == "BREAKOUT_BUY"),
        "breakdown": sum(1 for r in all_rows if r["Darvas_Signal"] == "BREAKDOWN_SELL"),
        "gc": sum(1 for r in all_rows if r["GC_Signal"] == "GOLDEN_CROSS"),
        "above_200": sum(1 for r in all_rows if r["GC_Signal"] in GOLDEN_OR_ABOVE),
    }


def screens_passed(ohlc: dict, res: dict) -> int:
    """How many of the six screens (Darvas, GC, Piotroski, CC, MF, BC) a stock passes."""
    return sum(
        [
            ohlc.get("Darvas_Signal") == "BREAKOUT_BUY",
            ohlc.get("GC_Signal") in GOLDEN_OR_ABOVE,
            bool(res.get("f_strong")),
            bool(res.get("qualifies_cc")),
            bool(res.get("qualifies_mf")),
            bool(res.get("qualifies_bc")),
        ]
    )


def tally_screens(sc_counts: dict, res: dict) -> None:
    """Increment the per-screener pass counters from one fundamental_scan result."""
    for key, flag in _SCREEN_FLAGS:
        if res.get(flag):
            sc_counts[key] += 1


def gc_signal(gc: dict) -> str:
    """Golden-Crossover label from a compute_golden_crossover result."""
    if gc.get("gc_signal"):
        return "GOLDEN_CROSS"
    return "ABOVE_200DMA" if gc.get("dma50_above_200") else "BELOW_200DMA"
