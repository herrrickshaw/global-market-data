"""custom_screener metrics/evaluation edge cases and screener_kit orchestration (fully faked I/O)."""

import numpy as np
import pandas as pd
import pytest

import custom_screener as cs
import frames
from strategies.base import StockData
from tests.w4_helpers import fake_module, fresh_import


def _px(close, vol=1000.0, hl=0.01):
    c = np.asarray(close, float)
    idx = pd.date_range("2023-01-01", periods=len(c))
    return pd.DataFrame(
        {"Open": c, "High": c * (1 + hl), "Low": c * (1 - hl), "Close": c, "Volume": vol}, index=idx
    )


# ---------------- custom_screener ----------------
def test_rsi_all_gains_is_100_and_short_none():
    assert cs._rsi(pd.Series(np.arange(1.0, 30.0))) == 100.0
    assert cs._rsi(pd.Series(np.arange(1.0, 15.0))) is None  # needs n+1 = 15 points
    assert cs._rsi(pd.Series(np.arange(1.0, 16.0))) == 100.0
    flat_then_down = pd.Series(np.linspace(30, 1, 40))
    assert cs._rsi(flat_then_down) == pytest.approx(0.0)


def test_rsi_simple_mean_value():
    # 14 deltas: 7 up of +2, 7 down of -1  -> mean up 1.0, mean down 0.5 -> RS 2 -> RSI 66.667
    steps = [2, -1] * 7
    s = pd.Series(np.concatenate([[100.0], 100.0 + np.cumsum(steps)]))
    assert cs._rsi(s) == pytest.approx(100 - 100 / 3)


def test_ret_boundary():
    s = pd.Series([100.0, 110.0, 121.0])
    assert cs._ret(s, 2) == pytest.approx(21.0)
    assert cs._ret(s, 1) == pytest.approx(10.0)
    assert cs._ret(s, 3) is None  # needs len > n


def test_ccc_helper_variants():
    assert cs._ccc({"ccc": 12}) == 12 and cs._ccc({}) is None
    f = {"inventory": 100, "receivables": 50, "payables": 25, "cogs": 365, "revenue": 730}
    assert cs._ccc(f) == pytest.approx(100 + 25 - 25)
    assert cs._ccc({"inventory": 10, "cogs": 0}) == 0  # zero denominator contributes 0 days
    assert cs._ccc({"receivables": 10, "revenue": None}) == 0


def test_compute_metrics_full_set_values():
    close = np.linspace(100, 200, 260)
    df = _px(close, vol=1000.0)
    df.loc[df.index[-1], "Volume"] = 3000.0
    m = cs.compute_metrics(StockData("T", ohlcv=df, fundamentals={"roe": 20}))
    assert m["roe"] == 20 and m["ltp"] == 200.0
    assert m["ret_21"] == pytest.approx(round((200 / close[-22] - 1) * 100, 2))
    assert m["ret_252"] == pytest.approx(round((200 / close[-253] - 1) * 100, 2))
    assert m["above_50dma"] is True and m["above_200dma"] is True
    assert m["sma50"] == pytest.approx(round(close[-50:].mean(), 2))
    assert m["rsi14"] == 100.0
    assert m["max_drawdown"] == 0.0 and m["vol_ann"] > 0
    assert m["dist_52w_high"] == pytest.approx(round((200 * 1.01 - 200) / (200 * 1.01) * 100, 2))
    assert m["dist_52w_low"] > 0
    assert m["avg_vol_20"] == 1000.0 and m["vol_ratio"] == 3.0
    assert m["atr14_pct"] == pytest.approx(
        round(float(((df["High"] - df["Low"]).tail(14).mean()) / 200 * 100), 1), abs=0.5
    )


def test_compute_metrics_short_history_and_no_data():
    m = cs.compute_metrics(StockData("T", ohlcv=_px(np.linspace(1, 2, 10))))
    assert "sma50" not in m and "sma200" not in m and "ret_21" not in m and "vol_ann" in m
    assert "rsi14" not in m  # needs 15 points
    m = cs.compute_metrics(StockData("T", ohlcv=_px(np.linspace(1, 2, 5))))
    assert "vol_ann" not in m and "max_drawdown" not in m  # len must exceed 5
    assert cs.compute_metrics(StockData("T", fundamentals={"a": 1})) == {"a": 1}
    assert (
        cs.compute_metrics(StockData("T", ohlcv=pd.DataFrame(), fundamentals={"ccc": 5}))["ccc"]
        == 5.0
    )


def test_compute_metrics_ccc_exposed_and_boundaries():
    m = cs.compute_metrics(StockData("T", fundamentals={"ccc": -10.04}))
    assert m["ccc"] == -10.0
    df = _px(np.linspace(1, 2, 21))
    assert "vol_ratio" in cs.compute_metrics(StockData("T", ohlcv=df))  # exactly 21 rows
    assert "vol_ratio" not in cs.compute_metrics(StockData("T", ohlcv=df.iloc[1:]))  # 20 rows
    assert "atr14_pct" in cs.compute_metrics(StockData("T", ohlcv=_px(np.linspace(1, 2, 15))))
    assert "atr14_pct" not in cs.compute_metrics(StockData("T", ohlcv=_px(np.linspace(1, 2, 14))))
    zero_vol = cs.compute_metrics(StockData("T", ohlcv=_px(np.linspace(1, 2, 30), vol=0.0)))
    assert zero_vol["avg_vol_20"] == 0.0 and "vol_ratio" not in zero_vol


def test_compute_metrics_without_high_low_columns():
    df = _px(np.linspace(10, 20, 60))[["Close", "Volume"]]
    m = cs.compute_metrics(StockData("T", ohlcv=df))
    assert m["dist_52w_high"] == 0.0 and "atr14_pct" not in m


def test_above_dma_boundary_requires_strictly_greater():
    flat = _px(np.full(60, 50.0))
    m = cs.compute_metrics(StockData("T", ohlcv=flat))
    assert m["above_50dma"] is False


def test_evaluate_dict_callable_unknown_op_and_type_errors():
    s = StockData("T", ohlcv=_px(np.linspace(1, 2, 30)), fundamentals={"roe": 20, "name": "x"})
    ok, m = cs.evaluate(s, {"roe": (">=", 20)})
    assert ok and m["roe"] == 20
    assert not cs.evaluate(s, {"roe": (">", 20)})[0]
    assert not cs.evaluate(s, {"missing": (">", 0)})[0]
    assert not cs.evaluate(s, {"name": (">", 3)})[0]  # str > int -> TypeError -> False
    with pytest.raises(ValueError, match="unknown operator '~'"):
        cs.evaluate(s, {"roe": ("~", 1)})
    assert cs.evaluate(s, lambda m: m["roe"] == 20)[0] is True
    assert cs.evaluate(s, lambda m: m["nope"] > 1)[0] is False  # exception -> False, not raised
    assert cs.evaluate(s, lambda m: 0)[0] is False
    assert cs.evaluate(s, {})[0] is True  # no criteria: everything passes


def _stocks():
    out = []
    for sym, roe in [("A", 10), ("B", 30), ("C", 20), ("D", 25)]:
        out.append(StockData(sym, "IN", fundamentals={"roe": roe, "pe": 40 - roe}))
    return out


def test_screen_rank_top_show_ascending():
    r = cs.screen(_stocks(), {"roe": (">=", 20)}, rank_by="roe")
    assert list(r["Symbol"]) == ["B", "D", "C"]
    r = cs.screen(_stocks(), {"roe": (">=", 20)}, rank_by="roe", ascending=True)
    assert list(r["Symbol"]) == ["C", "D", "B"]
    r = cs.screen(_stocks(), {}, rank_by="roe", top=2)
    assert list(r["Symbol"]) == ["B", "D"]
    r = cs.screen(_stocks(), {}, show=["pe", "bogus"])
    assert list(r.columns) == ["Symbol", "Market", "pe"]
    assert list(cs.screen(_stocks(), {})["Symbol"]) == ["A", "B", "C", "D"]  # input order kept
    assert list(cs.screen(_stocks(), {}, rank_by="nope")["Symbol"]) == ["A", "B", "C", "D"]
    assert cs.screen(_stocks(), {"roe": (">", 99)}).empty
    assert list(cs.screen(_stocks(), {}, top=0)["Symbol"]) == [
        "A",
        "B",
        "C",
        "D",
    ]  # falsy top = all
    assert list(cs.screen([], {}).columns) == []


# ---------------- screener_kit ----------------
@pytest.fixture
def kit(monkeypatch, tmp_path):
    monkeypatch.setenv("BHAV_CACHE", str(tmp_path / "live"))
    k = fresh_import(monkeypatch, "screener_kit")
    seed = tmp_path / "seed"
    seed.mkdir()
    monkeypatch.setattr(k, "SEED_DIR", seed)
    return k


def _seed(path, syms=("A", "B")):
    frames.write_seed({s: _px(np.linspace(10, 20, 30)) for s in syms}, path)


def test_seed_name_and_live_path_precedence(kit):
    assert (
        kit._seed_name("IN") == "cleaned_long.parquet"
        and kit._seed_name("US") == "cleaned_long_US.parquet"
    )
    assert (
        kit._live_path("US") == kit.SEED_DIR / "cleaned_long_US.parquet"
    )  # nothing exists -> seed path
    (kit.SEED_DIR / "cleaned_long_US.parquet").write_bytes(b"s")
    assert kit._live_path("US") == kit.SEED_DIR / "cleaned_long_US.parquet"
    (kit.CACHE / "cleaned_long_US.parquet").write_bytes(b"l")
    assert kit._live_path("US") == kit.CACHE / "cleaned_long_US.parquet"


def test_markets_lists_only_existing(kit):
    assert kit.markets() == []
    (kit.SEED_DIR / "cleaned_long.parquet").write_bytes(b"x")
    (kit.CACHE / "cleaned_long_JP.parquet").write_bytes(b"x")
    assert kit.markets() == ["IN", "JP"]


def test_bootstrap_copies_only_missing_and_builds_store(kit, monkeypatch, capsys):
    (kit.SEED_DIR / "cleaned_long.parquet").write_bytes(b"in-seed")
    (kit.SEED_DIR / "cleaned_long_US.parquet").write_bytes(b"us-seed")
    (kit.CACHE / "cleaned_long_US.parquet").write_bytes(b"us-live")  # already live: not overwritten
    fake_module(monkeypatch, "market_memory", ensure_stm_seeds=lambda verbose: None)
    built = []
    fake_module(monkeypatch, "bhavcopy_store", build=lambda verbose: built.append(verbose) or 7)
    r = kit.bootstrap()
    assert r == {"seeded": ["IN"], "cache": str(kit.CACHE)}
    assert (kit.CACHE / "cleaned_long.parquet").read_bytes() == b"in-seed"
    assert (kit.CACHE / "cleaned_long_US.parquet").read_bytes() == b"us-live"
    assert built == [True]
    out = capsys.readouterr().out
    assert "seeded 1 markets" in out and "store ready: 7 symbols" in out
    kit.bootstrap(rebuild_store=False, verbose=False)
    assert built == [True] and capsys.readouterr().out == ""


def test_bootstrap_survives_stm_derivation_failure(kit, monkeypatch):
    fake_module(monkeypatch, "market_memory", ensure_stm_seeds=lambda verbose: 1 / 0)
    assert kit.bootstrap(rebuild_store=False, verbose=False)["seeded"] == []


def test_load_prefers_datalink_then_falls_back(kit, monkeypatch):
    fake_module(monkeypatch, "datalink", load_market=lambda m, t: {"VIA": m, "t": t})
    assert kit.load("US", 5.0) == {"VIA": "US", "t": 5.0}
    fake_module(monkeypatch, "datalink", load_market=lambda m, t: 1 / 0)
    assert kit.load("US") == {}  # no data anywhere
    _seed(kit.SEED_DIR / "cleaned_long_US.parquet", ("A", "B"))
    assert set(kit.load("US")) == {"A", "B"}


def test_load_fallback_liquidity_filter(kit, monkeypatch):
    fake_module(monkeypatch, "datalink", load_market=lambda m, t: 1 / 0)
    _seed(kit.SEED_DIR / "cleaned_long_US.parquet", ("A", "B"))
    seen = {}
    fake_module(
        monkeypatch,
        "liquidity",
        liquid_symbols=lambda m, min_usd: seen.update(m=m, u=min_usd) or ["B"],
    )
    assert set(kit.load("US", 1e6)) == {"B"} and seen == {"m": "US", "u": 1e6}
    assert set(kit.load("US", 0)) == {"A", "B"}  # zero threshold: no filter
    fake_module(monkeypatch, "liquidity", liquid_symbols=lambda m, min_usd: 1 / 0)
    assert set(kit.load("US", 1e6)) == {"A", "B"}  # filter failure keeps everything


def test_get_prefers_store_then_scans_markets(kit, monkeypatch):
    df = _px([1, 2, 3])
    fake_module(monkeypatch, "bhavcopy_store", get=lambda s: df if s == "X" else None)
    assert kit.get("X") is df
    loaded = []
    monkeypatch.setattr(kit, "load", lambda m: loaded.append(m) or ({"Y": df} if m == "JP" else {}))
    assert kit.get("Y") is df and loaded == [
        "IN",
        "US",
        "JP",
    ]  # stops at the first market holding it
    assert kit.get("NOPE") is None
    fake_module(monkeypatch, "bhavcopy_store", get=lambda s: 1 / 0)
    assert kit.get("Y") is df


def test_update_in_delegates_to_bhavcopy(kit, monkeypatch):
    seen = []
    fake_module(monkeypatch, "bhavcopy_history", fetch_history=lambda verbose: seen.append(verbose))
    assert kit.update("IN", verbose=False) == {"market": "IN", "source": "bhavcopy"} and seen == [
        False
    ]


def test_update_without_seed_reports_error(kit, monkeypatch):
    fake_module(monkeypatch, "data_sources", fetch=lambda *a, **k: 1 / 0)
    monkeypatch.setattr(kit, "load", lambda m: {})
    assert kit.update("US") == {"market": "US", "error": "no seed; run a full fetch first"}


@pytest.mark.parametrize(
    "gap,expected", [(0, "5d"), (7, "5d"), (8, "1mo"), (35, "1mo"), (36, "3mo")]
)
def test_update_fetch_period_by_staleness(kit, monkeypatch, gap, expected):
    last = pd.Timestamp.today().normalize() - pd.Timedelta(days=gap)
    base = {
        "A": pd.DataFrame(
            {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
            index=[last],
        )
    }
    monkeypatch.setattr(kit, "load", lambda m: base)
    seen = {}
    fake_module(
        monkeypatch, "data_sources", fetch=lambda syms, **kw: seen.update(kw=kw, syms=syms) or {}
    )
    fake_module(monkeypatch, "bhavcopy_store", build=lambda verbose: None)
    kit.update("US", verbose=False)
    assert seen["kw"] == {
        "order": ("yahoo", "stooq"),
        "period": expected,
        "min_bars": 1,
        "verbose": False,
    }
    assert seen["syms"] == ["A"]


def test_update_unchanged_symbols_are_rewritten_and_counted(kit, monkeypatch, capsys):
    last = pd.Timestamp.today().normalize() - pd.Timedelta(days=2)
    base = {"A": _px(np.linspace(10, 11, 5)), "B": _px(np.linspace(20, 21, 5))}
    monkeypatch.setattr(kit, "load", lambda m: base)
    fake_module(monkeypatch, "data_sources", fetch=lambda *a, **k: {})
    built = []
    fake_module(monkeypatch, "bhavcopy_store", build=lambda verbose: built.append(verbose))
    r = kit.update("US")
    assert r == {"market": "US", "updated": 0} and built == [False]
    assert set(frames.read_seed(kit.CACHE / "cleaned_long_US.parquet")) == {"A", "B"}
    assert "0 symbols got new bars; store rebuilt" in capsys.readouterr().out


@pytest.mark.xfail(
    strict=True,
    raises=ValueError,
    reason="screener_kit.update: `clean_ohlcv(...) or df` evaluates the truthiness of a DataFrame, "
    "which pandas rejects ('truth value of a DataFrame is ambiguous') - any refreshed symbol crashes the update",
)
def test_update_merges_fresh_bars_into_seed(kit, monkeypatch):
    old = _px(np.linspace(10, 11, 5))
    new = _px(np.linspace(11, 12, 3))
    new.index = old.index[-1:].append(
        pd.date_range(old.index[-1] + pd.Timedelta(days=1), periods=2)
    )
    monkeypatch.setattr(kit, "load", lambda m: {"A": old})
    fake_module(monkeypatch, "data_sources", fetch=lambda *a, **k: {"A": new})
    fake_module(monkeypatch, "bhavcopy_store", build=lambda verbose: None)
    r = kit.update("US", verbose=False)
    assert r == {"market": "US", "updated": 1}


def test_india_ccc_sources(kit, monkeypatch):
    fake_module(monkeypatch, "datalink", ccc_map_cached=lambda: {"A": 1.0})
    assert kit._india_ccc() == {"A": 1.0}
    fake_module(monkeypatch, "datalink", ccc_map_cached=lambda: 1 / 0)
    fake_module(monkeypatch, "screener_in", ccc_map=lambda: {"A": 2.0, "B": float("nan")})
    assert kit._india_ccc() == {"A": 2.0}  # NaN dropped
    fake_module(monkeypatch, "screener_in", ccc_map=lambda: 1 / 0)
    assert kit._india_ccc() == {}


def test_stocks_attach_ccc_only_for_india(kit, monkeypatch):
    data = {"A": _px([1, 2, 3]), "B": _px([1, 2, 3])}
    seen = []
    monkeypatch.setattr(kit, "load", lambda m, t=0.0: seen.append((m, t)) or data)
    monkeypatch.setattr(kit, "_india_ccc", lambda: {"A": 12.0})
    ins = {s.symbol: s for s in kit._stocks("IN", 5.0)}
    assert (
        ins["A"].fundamentals == {"ccc": 12.0}
        and ins["B"].fundamentals == {}
        and ins["A"].market == "IN"
    )
    assert seen == [("IN", 5.0)]
    monkeypatch.setattr(kit, "_india_ccc", lambda: 1 / 0)
    assert all(s.fundamentals == {} for s in kit._stocks("US"))  # CCC never consulted for non-IN


class _Mod:
    def __init__(self, fn):
        self.fn = fn

    def screen(self, sd):
        return self.fn(sd)


class _R:
    def __init__(self, sym, score, passed=True):
        self.passed, self.sym, self.score = passed, sym, score

    def row(self):
        return {"Symbol": self.sym, "Score": self.score}


def _fake_strategies(monkeypatch, kit, fn):
    monkeypatch.setattr(kit.st, "STRATEGIES", {"s1": _Mod(fn)})
    monkeypatch.setattr(
        kit, "_stocks", lambda m, t=0.0: [StockData(s, m) for s in ("A", "B", "C", "D")]
    )
    fake_module(
        monkeypatch,
        "liquidity",
        annotate=lambda df: df.assign(Liquidity="High") if not df.empty else df,
    )


def test_screen_unknown_strategy(kit):
    with pytest.raises(ValueError, match="unknown strategy"):
        kit.screen("nope")


def test_screen_filters_sorts_tops_and_annotates(kit, monkeypatch):
    scores = {"A": 1, "B": 9, "C": 5, "D": 7}

    def fn(sd):
        if sd.symbol == "C":
            raise RuntimeError  # failure isolated
        return _R(sd.symbol, scores[sd.symbol], passed=sd.symbol != "D")

    _fake_strategies(monkeypatch, kit, fn)
    out = kit.screen("s1")
    assert list(out["Symbol"]) == ["B", "A"] and set(out["Liquidity"]) == {"High"}
    assert list(kit.screen("s1", top=1)["Symbol"]) == ["B"]
    assert list(kit.screen("s1", top=0)["Symbol"]) == ["B", "A"]


def test_screen_no_hits_and_none_results(kit, monkeypatch):
    _fake_strategies(monkeypatch, kit, lambda sd: None)
    assert kit.screen("s1").empty
    _fake_strategies(monkeypatch, kit, lambda sd: _R(sd.symbol, 1, False))
    assert kit.screen("s1", top=3).empty


def test_screen_keeps_order_without_score_column(kit, monkeypatch):
    class NoScore(_R):
        def row(self):
            return {"Symbol": self.sym}

    _fake_strategies(monkeypatch, kit, lambda sd: NoScore(sd.symbol, 0))
    assert list(kit.screen("s1")["Symbol"]) == ["A", "B", "C", "D"]


def test_custom_screen_passthrough_and_liquidity_failure(kit, monkeypatch):
    seen = {}
    monkeypatch.setattr(kit, "_stocks", lambda m, t=0.0: seen.update(m=m, t=t) or [])
    monkeypatch.setattr(
        kit.cs,
        "screen",
        lambda stocks, crit, **kw: seen.update(kw=kw, crit=crit) or pd.DataFrame({"Symbol": ["A"]}),
    )
    fake_module(monkeypatch, "liquidity", annotate=lambda df: 1 / 0)
    out = kit.custom_screen(
        {"roe": (">", 1)},
        market="US",
        rank_by="roe",
        top=3,
        ascending=True,
        show=["roe"],
        min_turnover_usd=7.0,
    )
    assert list(out["Symbol"]) == ["A"]  # annotate failure -> frame returned unchanged
    assert seen["m"] == "US" and seen["t"] == 7.0
    assert seen["kw"] == {"rank_by": "roe", "top": 3, "ascending": True, "show": ["roe"]}
    kit.custom_screen({})
    assert (
        seen["kw"] == {"rank_by": None, "top": 50, "ascending": False, "show": None}
        and seen["m"] == "IN"
    )
