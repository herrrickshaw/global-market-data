"""Shared Darvas-box primitives used by the daily-report modules.

Box formation uses ONLY historical bars (every bar except the last); the current bar is
excluded so its low cannot drag the box bottom down and hide a breakdown.
"""

import pandas as pd


def find_ohlc_col(df: pd.DataFrame, candidates: list[str]):
    """First column whose name contains any candidate (case-insensitive, in candidate order)."""
    for c in candidates:
        match = next((col for col in df.columns if c.upper() in col.upper()), None)
        if match:
            return match
    return None


def ohlc_series(df: pd.DataFrame, h_col: str, l_col: str, c_col: str):
    """High, low and close columns as float lists (non-numeric -> 0)."""
    return tuple(
        pd.to_numeric(df[col], errors="coerce").fillna(0).tolist() for col in (h_col, l_col, c_col)
    )


def insufficient(note: str) -> dict:
    return {"signal": "INSUFFICIENT_DATA", "box_top": None, "box_bottom": None, "note": note}


def no_box(note: str, box_top=None) -> dict:
    return {"signal": "NO_BOX", "box_top": box_top, "box_bottom": None, "note": note}


def confirmed_box_top(highs: list[float], confirm: int):
    """Most recent high not exceeded by the next ``confirm`` highs. Returns (index, top) or (None, None)."""
    for i in range(len(highs) - confirm - 1, -1, -1):
        candidate = highs[i]
        if candidate == 0:
            continue
        window = highs[i + 1 : i + 1 + confirm]
        if len(window) == confirm and all(h < candidate for h in window):
            return i, candidate
    return None, None


def confirmed_box_bottom(segment: list[float], confirm: int):
    """First low (from the box-top day onward) not undercut for ``confirm`` bars.

    Falls back to the minimum positive low; None if there is none.
    """
    for i in range(len(segment) - confirm):
        candidate = segment[i]
        if candidate == 0:
            continue
        window = segment[i + 1 : i + 1 + confirm]
        if len(window) == confirm and all(low > candidate for low in window):
            return candidate
    valid = [low for low in segment if low > 0]
    return min(valid) if valid else None


def classify_box(current: float, box_top: float, box_bottom: float, confirm: int, n_bars: int):
    """Classify today's close against the box and build the result dict."""
    if current > box_top:
        signal = "BREAKOUT_BUY"
    elif current < box_bottom:
        signal = "BREAKDOWN_SELL"
    else:
        signal = "IN_BOX"

    box_range = box_top - box_bottom
    pos_in_box = ((current - box_bottom) / box_range * 100) if box_range else 0
    upside_to_top = ((box_top - current) / current * 100) if current else 0
    return {
        "signal": signal,
        "box_top": round(box_top, 2),
        "box_bottom": round(box_bottom, 2),
        "current_price": round(current, 2),
        "box_range": round(box_range, 2),
        "position_in_box_pct": round(pos_in_box, 1),
        "upside_to_top_pct": round(upside_to_top, 2),
        "confirm_days": confirm,
        "data_points": n_bars,
    }
