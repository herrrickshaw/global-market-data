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
