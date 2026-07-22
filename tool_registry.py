# tool_registry.py
# ================
# Lightweight extensibility layer — add new screeners, news providers, and
# analyses with a few lines instead of full boilerplate. The goal is to MINIMISE
# code length: a new capability becomes one decorated function, auto-discovered
# by the pipelines.
#
# ─────────────────────────────────────────────────────────────────────────────
# THE TWO ANALYTICAL APPROACHES (this is the conceptual map of the whole system)
# ─────────────────────────────────────────────────────────────────────────────
#
# 1) FUNDAMENTAL / HISTORICAL APPROACH  (pipeline_historical.py)
#    WHAT : ranks stocks from their own price & accounting history.
#    DATA : 5-year OHLC + financial statements (Parquet cache, offline).
#    TOOLS: the 6 screeners — Darvas, Golden Cross, Piotroski, Coffee Can,
#           Magic Formula, Bull Cartel — each a Specification (objective rule).
#    OUTPUT: "Stock Picks Based on Fundamentals" — deterministic, repeatable,
#            backtestable. A stock either passes a numeric rule or it doesn't.
#    STRENGTH: no look-ahead, no third-party text, fully reproducible.
#    LIMIT  : backward-looking; says nothing about today's catalysts.
#
# 2) NEWS / SENTIMENT APPROACH  (pipeline_news.py)
#    WHAT : reads what the market is SAYING about a stock right now.
#    DATA : live headlines (RSS: Moneycontrol/ET/BusinessLine/LiveMint for IN,
#           CNBC/MarketWatch for US; + optional Marketaux/AlphaVantage/Finnhub).
#    TOOLS: news providers → company-name matching → VADER/provider sentiment.
#    OUTPUT: "Talk on the Street" — forward-looking, noisy, fast-moving.
#    STRENGTH: captures catalysts (results, upgrades, deals) the price hasn't
#              fully reflected yet.
#    LIMIT  : noisy, provider-dependent, can lead OR lag price; not backtestable.
#
# CONVERGENCE = the two approaches agreeing (fundamentally strong AND positive
# news). It is a CROSS-CHECK, never a buy signal — each view has different
# failure modes, so agreement is informative but not predictive.
#
# ─────────────────────────────────────────────────────────────────────────────
# HOW TO ADD A NEW TOOL WITH MINIMAL CODE
# ─────────────────────────────────────────────────────────────────────────────
#   from tool_registry import screener, news_source, analysis
#
#   @screener("high_roe", "ROE > 20% and low debt")
#   def high_roe(c):                       # c = ScreeningCandidate
#       roe = c._row(c.income_stmt, "Net Income") / (c.book_value or 1e9)
#       return roe and roe > 0.20
#
#   @news_source("reuters_in", feeds=["https://.../reuters_india.rss"])
#   def _():  ...                          # feeds auto-wired; no class needed
#
#   @analysis("momentum_rank")
#   def momentum_rank(ohlc_map): ...       # appears in pipeline_historical --stages
#
# Registered items are discoverable via list_screeners() / list_news_sources()
# / list_analyses() and run via run_screener() / run_analysis(). The full-scan
# and report pipelines can iterate the registry instead of hard-coding each one.

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional

# ── Registry stores ───────────────────────────────────────────────────────────


@dataclass
class RegisteredScreener:
    key: str
    description: str
    fn: Callable  # (ScreeningCandidate) -> bool
    tier_weight: int = 1  # contribution to multi-screen count


@dataclass
class RegisteredNewsSource:
    key: str
    feeds: List[str] = field(default_factory=list)
    market: str = "IN"
    fn: Optional[Callable] = None  # custom fetch(ticker)->list, else RSS default


@dataclass
class RegisteredAnalysis:
    key: str
    description: str
    fn: Callable  # (context) -> result


_SCREENERS: Dict[str, RegisteredScreener] = {}
_NEWS_SOURCES: Dict[str, RegisteredNewsSource] = {}
_ANALYSES: Dict[str, RegisteredAnalysis] = {}


# ── Decorators (the "method to create more tools") ────────────────────────────


def screener(key: str, description: str = "", tier_weight: int = 1):
    """Register a screener. The wrapped fn takes a ScreeningCandidate → bool.

    Adding a screener is now ~5 lines instead of a sheet + loop + spec class.
    """

    def deco(fn: Callable) -> Callable:
        _SCREENERS[key] = RegisteredScreener(key, description or fn.__doc__ or key, fn, tier_weight)
        return fn

    return deco


def news_source(key: str, feeds: List[str] = None, market: str = "IN"):
    """Register a news source. Provide RSS `feeds` for the default fetcher, or
    decorate a custom fetch(ticker, market)->list[dict] function."""

    def deco(fn: Callable) -> Callable:
        custom = None if (feeds and fn.__name__ == "_") else fn
        _NEWS_SOURCES[key] = RegisteredNewsSource(key, feeds or [], market, custom)
        return fn

    return deco


def analysis(key: str, description: str = ""):
    """Register an analysis tool that the historical pipeline can run as a stage."""

    def deco(fn: Callable) -> Callable:
        _ANALYSES[key] = RegisteredAnalysis(key, description or fn.__doc__ or key, fn)
        return fn

    return deco


# ── Discovery + execution ─────────────────────────────────────────────────────


def list_screeners() -> List[str]:
    return list(_SCREENERS)


def list_news_sources() -> List[str]:
    return list(_NEWS_SOURCES)


def list_analyses() -> List[str]:
    return list(_ANALYSES)


def run_screener(key: str, candidate) -> bool:
    s = _SCREENERS.get(key)
    return bool(s.fn(candidate)) if s else False


def run_all_screeners(candidate) -> List[str]:
    """Return the keys of every registered screener the candidate passes.
    Lets the scan loop replace N hard-coded if-blocks with one registry pass."""
    return [k for k, s in _SCREENERS.items() if _safe(s.fn, candidate)]


def run_analysis(key: str, context):
    a = _ANALYSES.get(key)
    return a.fn(context) if a else None


def get_news_source(key: str) -> Optional[RegisteredNewsSource]:
    return _NEWS_SOURCES.get(key)


def _safe(fn, arg) -> bool:
    try:
        return bool(fn(arg))
    except Exception:
        return False


# ── Bridge: auto-register the existing 6 screeners from specifications.py ──────
# This shows how the legacy screeners fold into the registry so both old and new
# code paths share one source of truth. Importing is lazy/optional.


def register_builtin_screeners() -> int:
    """Wrap the existing Specification classes as registry entries (idempotent)."""
    try:
        import sys
        from pathlib import Path

        sys.path.insert(0, str(Path(__file__).parent / "stock_ddd"))
        from domain.screening.specifications import (
            BullCartelSpec,
            CoffeeCanSpec,
            DarvasBoxSpec,
            GoldenCrossSpec,
            MagicFormulaSpec,
            PiotroskiSpec,
        )
    except Exception:
        return 0
    specs = {
        "darvas": DarvasBoxSpec(),
        "golden_cross": GoldenCrossSpec(),
        "piotroski": PiotroskiSpec(),
        "coffee_can": CoffeeCanSpec(),
        "magic_formula": MagicFormulaSpec(),
        "bull_cartel": BullCartelSpec(),
    }
    for k, spec in specs.items():
        _SCREENERS[k] = RegisteredScreener(k, spec.explain(), spec.is_satisfied_by)
    return len(specs)


# ── Demo / self-test ──────────────────────────────────────────────────────────

if __name__ == "__main__":
    # Example: add a brand-new screener in 4 lines.
    @screener("near_52w_high", "Within 3% of the 52-week high")
    def near_high(c):
        df = getattr(c, "ohlc_df", None)
        if df is None or len(df) < 252:
            return False
        last = float(df["Close"].iloc[-1])
        hi = float(df["High"].tail(252).max())
        return hi > 0 and (last - hi) / hi >= -0.03

    @analysis("count_bars", "Trivial demo analysis")
    def count_bars(ohlc_map):
        return {sym: len(df) for sym, df in (ohlc_map or {}).items()}

    n = register_builtin_screeners()
    print(f"Registered screeners ({len(list_screeners())}): {list_screeners()}")
    print(f"  built-ins wired: {n}")
    print(f"Analyses: {list_analyses()}")
    print(f"News sources: {list_news_sources()}")
    print("\n→ A new screener/analysis/news source is now ONE decorated function.")
