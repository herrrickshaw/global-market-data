# stock_enricher.py
# ==================
# Enriches screener results with company name, PE ratio, PE zone,
# exchange, and sector — used by the daily mailer to generate
# properly formatted stock tables.
#
# PE ZONE CLASSIFICATION (sector-aware)
# ──────────────────────────────────────
# Indian market PE zones vary dramatically by sector:
#   FMCG / Consumer:    normal PE 45-70x  → "sell zone" starts at 80x
#   IT Services:        normal PE 20-35x  → "sell zone" starts at 40x
#   Banking / NBFC:     normal PE 10-18x  → "sell zone" starts at 22x
#   Pharma:             normal PE 25-40x  → "sell zone" starts at 50x
#   Energy / PSU:       normal PE 8-15x   → "sell zone" starts at 20x
#   Auto:               normal PE 15-25x  → "sell zone" starts at 30x
#   Infrastructure:     normal PE 18-30x  → "sell zone" starts at 35x
#   Default:            normal PE 15-25x  → "sell zone" starts at 35x
#
# US market PE zones:
#   Tech / Growth:      normal PE 25-50x
#   Financials:         normal PE 10-16x
#   Healthcare:         normal PE 18-30x
#   Consumer Discretionary: 20-35x
#   Default:            15-25x
#
# Zone labels:
#   🟢 BUY ZONE       PE below lower threshold (cheap vs sector)
#   🟡 FAIR VALUE     PE within normal range
#   🟠 CAUTION        PE above normal, approaching expensive
#   🔴 SELL ZONE      PE significantly above sector norms
#   ⚪ N/A            PE not available or negative

import warnings
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

warnings.filterwarnings("ignore")

try:
    import yfinance as yf

    _YF_OK = True
except ImportError:
    _YF_OK = False

# ── Sector PE thresholds ──────────────────────────────────────────────────────
# (buy_max, fair_max, caution_max) — above caution_max = SELL ZONE
# These are approximate Indian market benchmarks as of 2025-26

_IN_SECTOR_PE = {
    # Sector keyword (lowercase) → (buy, fair, caution)
    "bank": (12, 18, 22),
    "finance": (12, 20, 25),
    "nbfc": (15, 22, 28),
    "insurance": (20, 35, 50),
    "fmcg": (40, 60, 80),
    "consumer": (35, 55, 75),
    "food": (40, 60, 80),
    "beverage": (40, 60, 80),
    "household": (35, 55, 70),
    "personal": (35, 55, 70),
    "information technology": (18, 28, 38),
    "software": (18, 28, 38),
    "it service": (18, 28, 38),
    "pharma": (22, 35, 50),
    "healthcare": (25, 40, 55),
    "hospital": (30, 50, 70),
    "diagnostic": (30, 55, 75),
    "energy": (8, 15, 22),
    "oil": (8, 14, 20),
    "gas": (8, 14, 20),
    "power": (10, 18, 25),
    "utility": (10, 18, 25),
    "auto": (14, 22, 30),
    "automobile": (14, 22, 30),
    "vehicle": (14, 22, 30),
    "tyre": (15, 22, 30),
    "chemical": (20, 32, 45),
    "specialty chem": (22, 35, 50),
    "cement": (20, 30, 40),
    "infrastructure": (18, 28, 38),
    "construction": (18, 28, 38),
    "real estate": (20, 35, 50),
    "realty": (20, 35, 50),
    "metal": (8, 15, 22),
    "steel": (8, 14, 20),
    "aluminium": (8, 14, 20),
    "mining": (8, 14, 20),
    "telecom": (20, 35, 55),
    "media": (15, 25, 40),
    "retail": (25, 45, 65),
    "textile": (10, 18, 25),
    "logistics": (15, 25, 35),
    "aviation": (15, 30, 50),
    "hotel": (25, 45, 70),
    "default": (15, 25, 35),
}

_US_SECTOR_PE = {
    "technology": (22, 35, 50),
    "software": (22, 35, 50),
    "semiconductor": (18, 30, 45),
    "communication": (20, 32, 45),
    "financial": (10, 16, 22),
    "bank": (8, 14, 20),
    "insurance": (10, 16, 22),
    "healthcare": (18, 30, 45),
    "pharmaceutical": (20, 32, 48),
    "biotechnology": (25, 45, 80),
    "consumer discretionary": (18, 28, 40),
    "consumer staples": (20, 28, 38),
    "energy": (10, 18, 28),
    "utilities": (14, 20, 28),
    "real estate": (30, 50, 80),
    "industrials": (15, 22, 32),
    "materials": (12, 20, 28),
    "default": (15, 22, 32),
}


def _classify_pe(pe: float, sector: str, market: str = "IN") -> dict:
    """
    Classify a PE ratio into buy/fair/caution/sell zone based on sector.
    Returns dict with zone label, emoji, and thresholds.
    """
    if pe is None or pd.isna(pe) or pe <= 0:
        return {
            "zone": "N/A",
            "emoji": "⚪",
            "pe_str": "N/A",
            "buy": None,
            "fair": None,
            "caution": None,
        }

    sector_lower = (sector or "").lower()
    thresholds = _IN_SECTOR_PE if market == "IN" else _US_SECTOR_PE
    buy, fair, caution = thresholds["default"]

    for keyword, thresh in thresholds.items():
        if keyword in sector_lower:
            buy, fair, caution = thresh
            break

    pe_str = f"{pe:.1f}x"
    if pe <= buy:
        return {
            "zone": "BUY ZONE",
            "emoji": "🟢",
            "pe_str": pe_str,
            "buy": buy,
            "fair": fair,
            "caution": caution,
        }
    elif pe <= fair:
        return {
            "zone": "FAIR VALUE",
            "emoji": "🟡",
            "pe_str": pe_str,
            "buy": buy,
            "fair": fair,
            "caution": caution,
        }
    elif pe <= caution:
        return {
            "zone": "CAUTION",
            "emoji": "🟠",
            "pe_str": pe_str,
            "buy": buy,
            "fair": fair,
            "caution": caution,
        }
    else:
        return {
            "zone": "SELL ZONE",
            "emoji": "🔴",
            "pe_str": pe_str,
            "buy": buy,
            "fair": fair,
            "caution": caution,
        }


def _fetch_one(symbol: str, suffix: str, market: str = "IN", retries: int = 3) -> dict:
    """Fetch company name, PE, sector, exchange for one stock."""
    result = {
        "symbol": symbol,
        "suffix": suffix,
        "company_name": symbol,
        "exchange": (
            "NSE"
            if suffix == ".NS"
            else ("BSE" if suffix == ".BO" else "NASDAQ" if market == "US" else "NYSE")
        ),
        "sector": "",
        "trailing_pe": None,
        "forward_pe": None,
        "pe_zone": "⚪ N/A",
        "pe_label": "N/A",
        "pe_thresholds": "",
    }
    if not _YF_OK:
        return result

    import time as _time

    for attempt in range(retries):
        try:
            ticker = yf.Ticker(f"{symbol}{suffix}")
            info = ticker.info or {}
            if not info or len(info) < 5:
                # Empty response — likely rate limited
                _time.sleep(3 * (attempt + 1))
                continue
            break
        except Exception as e:
            if "Rate" in str(e) or "429" in str(e) or "Too Many" in str(e):
                _time.sleep(5 * (attempt + 1))
                continue
            return result
    else:
        return result

    try:

        name = (info.get("shortName") or info.get("longName") or symbol).strip()
        # Clean up name — remove "Ltd", "Limited" etc. for compactness if very long
        if len(name) > 40:
            name = name[:38] + "…"
        result["company_name"] = name

        sector = info.get("sector") or info.get("industry") or ""
        result["sector"] = sector

        # Exchange from info
        exch = (info.get("exchange") or "").upper()
        if suffix == ".NS":
            result["exchange"] = "NSE"
        elif suffix == ".BO":
            result["exchange"] = "BSE"
        elif "NAS" in exch or "NGM" in exch or "NCM" in exch:
            result["exchange"] = "NASDAQ"
        elif "NYS" in exch:
            result["exchange"] = "NYSE"

        # PE
        t_pe = info.get("trailingPE")
        f_pe = info.get("forwardPE")
        result["trailing_pe"] = round(float(t_pe), 1) if t_pe else None
        result["forward_pe"] = round(float(f_pe), 1) if f_pe else None

        # Use trailing PE for zone classification (forward PE if trailing unavailable)
        pe_for_zone = result["trailing_pe"] or result["forward_pe"]
        pe_info = _classify_pe(pe_for_zone, sector, market)
        result["pe_zone"] = f"{pe_info['emoji']} {pe_info['zone']}"
        result["pe_label"] = pe_info["zone"]
        result["pe_thresholds"] = (
            f"Buy<{pe_info['buy']} Fair<{pe_info['fair']} " f"Caution<{pe_info['caution']}"
            if pe_info.get("buy")
            else ""
        )
    except Exception:
        pass

    return result


def enrich_stocks(
    df: pd.DataFrame,
    symbol_col: str = "Symbol",
    suffix_col: str = "Suffix",
    market: str = "IN",
    workers: int = 8,
) -> pd.DataFrame:
    """
    Enrich a screener results DataFrame with company name, PE, exchange.

    df:         DataFrame with at least a Symbol column
    symbol_col: name of the column containing tickers
    suffix_col: '.NS' / '.BO' / '' — if column missing, defaults to '.NS'
    market:     'IN' or 'US'
    workers:    parallel fetch threads

    Returns the same DataFrame with additional columns:
      Company_Name, Exchange, Sector, Trailing_PE, Forward_PE, PE_Zone, PE_Label
    """
    symbols = df[symbol_col].tolist()
    suffixes = (
        df[suffix_col].tolist()
        if suffix_col in df.columns
        else [".NS" if market == "IN" else ""] * len(symbols)
    )

    print(f"  Enriching {len(symbols)} stocks with PE + company name …")
    enriched = {}

    with ThreadPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(_fetch_one, sym, sfx, market): sym for sym, sfx in zip(symbols, suffixes)
        }
        done = 0
        for future in as_completed(futures):
            done += 1
            try:
                r = future.result()
                enriched[r["symbol"]] = r
            except Exception:
                pass
            if done % 50 == 0 or done == len(symbols):
                print(f"    {done}/{len(symbols)} enriched")

    # Merge back into DataFrame
    df = df.copy()
    df["Company_Name"] = df[symbol_col].map(lambda s: enriched.get(s, {}).get("company_name", s))
    df["Exchange"] = df[symbol_col].map(lambda s: enriched.get(s, {}).get("exchange", "NSE"))
    df["Sector"] = df[symbol_col].map(lambda s: enriched.get(s, {}).get("sector", ""))
    df["Trailing_PE"] = df[symbol_col].map(lambda s: enriched.get(s, {}).get("trailing_pe"))
    df["Forward_PE"] = df[symbol_col].map(lambda s: enriched.get(s, {}).get("forward_pe"))
    df["PE_Zone"] = df[symbol_col].map(lambda s: enriched.get(s, {}).get("pe_zone", "⚪ N/A"))
    df["PE_Label"] = df[symbol_col].map(lambda s: enriched.get(s, {}).get("pe_label", "N/A"))

    return df


def format_stock_display(row: dict) -> str:
    """
    Format one stock row as:
    COMPANY NAME [TICKER · EXCHANGE]
    """
    name = row.get("Company_Name", row.get("Symbol", ""))
    sym = row.get("Symbol", "")
    exch = row.get("Exchange", "NSE")
    return f"{name.upper()} [{sym} · {exch}]"


def build_exchange_groups(df: pd.DataFrame) -> dict:
    """
    Split a DataFrame into groups by exchange.
    Returns dict: {'NSE': df, 'BSE': df, 'NASDAQ': df, 'NYSE': df}
    """
    groups = {}
    if "Exchange" not in df.columns:
        return {"ALL": df}
    for exch in ["NSE", "BSE", "NASDAQ", "NYSE"]:
        sub = df[df["Exchange"] == exch]
        if not sub.empty:
            groups[exch] = sub.reset_index(drop=True)
    # Catch anything not in the four exchanges
    other = df[~df["Exchange"].isin(["NSE", "BSE", "NASDAQ", "NYSE"])]
    if not other.empty:
        groups["OTHER"] = other.reset_index(drop=True)
    return groups


def generate_stock_table_html(df: pd.DataFrame, market: str = "IN", max_rows: int = 15) -> str:
    """
    Generate an HTML <table> for one exchange group with:
    Company Name [Ticker · Exchange] | LTP | Upside% | PE | PE Zone

    Designed to be inserted directly into the daily email HTML template.
    """
    if df.empty:
        return "<p style='color:#999'>No qualifying stocks</p>"

    currency = "₹" if market == "IN" else "$"
    rows = df.head(max_rows)

    html = """
<table>
  <tr>
    <th>Company [Ticker · Exchange]</th>
    <th>LTP</th>
    <th>Change%</th>
    <th>Upside to Box%</th>
    <th>Trailing PE</th>
    <th>PE Zone</th>
    <th>Screens Passed</th>
  </tr>"""

    for _, r in rows.iterrows():
        sym = r.get("Symbol", "")
        name = r.get("Company_Name", sym)
        exch = r.get("Exchange", "NSE")
        ltp = r.get("LTP", "—")
        chg = r.get("Change%", 0) or 0
        upside = r.get("Upside_to_Top%", r.get("Upside%", "—"))
        tpe = r.get("Trailing_PE", None)
        pe_zone = r.get("PE_Zone", "⚪ N/A")
        screens = r.get("Screens_Passed", r.get("Darvas_Signal", "—"))

        # Formatting
        ltp_str = f"{currency}{float(ltp):,.2f}" if ltp != "—" else "—"
        chg_str = (
            f'<span class="pass">▲ {chg:+.2f}%</span>'
            if chg >= 0
            else f'<span class="fail">▼ {chg:.2f}%</span>'
        )
        upside_str = f"{float(upside):+.1f}%" if upside != "—" and upside is not None else "—"
        pe_str = f"{tpe:.1f}x" if tpe and not pd.isna(tpe) else "N/A"
        name_str = f"<strong>{name.upper()}</strong> [{sym} · {exch}]"

        # PE Zone colour
        zone_class = {"BUY ZONE": "pass", "FAIR VALUE": "", "CAUTION": "warn", "SELL ZONE": "fail"}
        zone_label = r.get("PE_Label", "N/A")
        zone_css = zone_class.get(zone_label, "")
        pe_zone_html = f'<span class="{zone_css}">{pe_zone}</span>'

        html += f"""
  <tr>
    <td>{name_str}</td>
    <td>{ltp_str}</td>
    <td>{chg_str}</td>
    <td>{upside_str}</td>
    <td>{pe_str}</td>
    <td>{pe_zone_html}</td>
    <td>{screens}</td>
  </tr>"""

    html += "\n</table>"
    return html
