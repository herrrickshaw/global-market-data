"""datalink (manifest, memoised loads, incremental LMDB store) and serving_layer (features, CDC)."""

import json
import os
import time
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

import frames
from tests.w4_helpers import fake_module, fresh_import


def _long(sym, n=30, base=10.0, start="2024-01-01"):
    idx = pd.date_range(start, periods=n)
    c = base + np.arange(n, dtype=float)
    return pd.DataFrame(
        {"Date": idx, "Open": c, "High": c, "Low": c, "Close": c, "Volume": 1000, "Symbol": sym}
    )


@pytest.fixture
def dl(monkeypatch, tmp_path):
    import datalink

    seed = tmp_path / "seed"
    (seed / "ltm").mkdir(parents=True)
    ref = tmp_path / "ref"
    ref.mkdir()
    live = tmp_path / "live"
    live.mkdir()
    monkeypatch.setattr(datalink, "HERE", tmp_path)
    monkeypatch.setattr(datalink, "SEED_DIR", seed)
    monkeypatch.setattr(datalink, "LTM_DIR", seed / "ltm")
    monkeypatch.setattr(datalink, "REF_DIR", ref)
    monkeypatch.setattr(datalink, "MANIFEST", seed / "data_manifest.json")
    monkeypatch.setattr(datalink, "STORE_STATE", seed / ".store_state.json")
    monkeypatch.setattr(datalink, "LIVE_CACHE", live)
    monkeypatch.setattr(datalink, "CCC_PARQUET", seed / "india_ccc_screen.parquet")
    monkeypatch.setattr(datalink, "_CCC_CACHE", None)
    monkeypatch.setattr(datalink, "_LOAD_CACHE", {})
    return datalink


def test_seed_names_and_ltm_path(dl):
    assert dl._seed_name("IN") == "cleaned_long.parquet"
    assert dl._seed_name("US") == "cleaned_long_US.parquet"
    assert dl._ltm_path("JP") == dl.LTM_DIR / "JP.parquet"


def test_stm_path_prefers_live_then_seed(dl):
    assert dl._stm_path("US") == dl.SEED_DIR / "cleaned_long_US.parquet"
    (dl.SEED_DIR / "cleaned_long_US.parquet").write_bytes(b"s")
    assert dl._stm_path("US") == dl.SEED_DIR / "cleaned_long_US.parquet"
    (dl.LIVE_CACHE / "cleaned_long_US.parquet").write_bytes(b"l")
    assert dl._stm_path("US") == dl.LIVE_CACHE / "cleaned_long_US.parquet"


def test_stm_path_derives_from_ltm_when_missing(dl, monkeypatch):
    (dl.LTM_DIR / "US.parquet").write_bytes(b"x")
    calls = []
    fake_module(monkeypatch, "market_memory", ensure_stm_seeds=lambda m, verbose: calls.append((m, verbose)))
    assert dl._stm_path("US") == dl.SEED_DIR / "cleaned_long_US.parquet"
    assert calls == [(["US"], False)]
    # no LTM -> no derive attempt
    calls.clear()
    dl._stm_path("JP")
    assert calls == []


def test_stm_path_derive_failure_swallowed(dl, monkeypatch):
    (dl.LTM_DIR / "US.parquet").write_bytes(b"x")

    def boom(m, verbose):
        raise RuntimeError

    fake_module(monkeypatch, "market_memory", ensure_stm_seeds=boom)
    assert dl._stm_path("US").name == "cleaned_long_US.parquet"


def test_sig_format(dl, tmp_path):
    p = tmp_path / "f"
    p.write_bytes(b"abc")
    os.utime(p, (1_700_000_000, 1_700_000_000))
    assert dl._sig(p) == "3:1700000000"


def test_load_market_missing_memoised_and_invalidated(dl, monkeypatch):
    assert dl.load_market("US") == {}
    p = dl.SEED_DIR / "cleaned_long_US.parquet"
    _long("A").to_parquet(p)
    reads = []
    real = pd.read_parquet
    monkeypatch.setattr(dl.pd, "read_parquet", lambda *a, **k: reads.append(a) or real(*a, **k))
    a = dl.load_market("US")
    b = dl.load_market("US")
    assert a is b and len(reads) == 1 and set(a) == {"A"}
    # a different turnover threshold is a different cache key
    dl.load_market("US", min_turnover_usd=0.0)
    assert len(reads) == 1
    # rewriting the seed changes size/mtime -> reload
    pd.concat([_long("A"), _long("B")]).to_parquet(p)
    os.utime(p, (time.time() + 5, time.time() + 5))
    assert set(dl.load_market("US")) == {"A", "B"} and len(reads) == 2
    dl.clear_cache()
    assert dl._LOAD_CACHE == {}


def test_load_market_ltm_tier_and_liquidity_filter(dl, monkeypatch):
    pd.concat([_long("A"), _long("B")]).to_parquet(dl._ltm_path("US"))
    assert set(dl.load_market("US", tier="ltm")) == {"A", "B"}
    got = {}

    def liquid_symbols(market, min_usd):
        got.update(market=market, min_usd=min_usd)
        return ["B"]

    fake_module(monkeypatch, "liquidity", liquid_symbols=liquid_symbols)
    assert set(dl.load_market("US", min_turnover_usd=5e6, tier="ltm")) == {"B"}
    assert got == {"market": "US", "min_usd": 5e6}
    # zero / negative threshold never consults the liquidity index
    got.clear()
    dl.clear_cache()
    dl.load_market("US", min_turnover_usd=-1, tier="ltm")
    assert got == {}


def test_load_market_liquidity_failure_keeps_all(dl, monkeypatch):
    pd.concat([_long("A"), _long("B")]).to_parquet(dl._ltm_path("US"))

    def boom(*a, **k):
        raise RuntimeError

    fake_module(monkeypatch, "liquidity", liquid_symbols=boom)
    assert set(dl.load_market("US", min_turnover_usd=1, tier="ltm")) == {"A", "B"}


def _ccc_parquet(dl, rows):
    pd.DataFrame(rows).to_parquet(dl.CCC_PARQUET)


def test_ccc_map_cached_fresh_file_no_network(dl, monkeypatch):
    _ccc_parquet(dl, {"Symbol": ["A", "B", "C"], "Cash_Cycle": ["-5", "x", "12"]})
    fake_module(monkeypatch, "screener_in", ccc_screen=lambda: 1 / 0)
    out = dl.ccc_map_cached()
    assert out == {"A": -5.0, "C": 12.0}  # NaN dropped
    assert dl.ccc_map_cached() is out  # memoised


def test_ccc_map_cached_stale_refetches_and_boundary(dl, monkeypatch):
    _ccc_parquet(dl, {"Symbol": ["OLD"], "Cash_Cycle": [1.0]})
    old = time.time() - 2 * 86400
    os.utime(dl.CCC_PARQUET, (old, old))
    fake_module(
        monkeypatch,
        "screener_in",
        ccc_screen=lambda: pd.DataFrame({"Symbol": ["NEW"], "Cash_Cycle": [7.0]}),
    )
    assert dl.ccc_map_cached() == {"NEW": 7.0}
    # file now fresh
    dl._CCC_CACHE = None
    fake_module(monkeypatch, "screener_in", ccc_screen=lambda: 1 / 0)
    assert dl.ccc_map_cached(max_age_days=1.0) == {"NEW": 7.0}
    dl._CCC_CACHE = None
    os.utime(dl.CCC_PARQUET, (time.time() - 3600, time.time() - 3600))
    fake_module(monkeypatch, "screener_in", ccc_screen=lambda: pd.DataFrame({"Symbol": ["Z"], "Cash_Cycle": [2.0]}))
    assert dl.ccc_map_cached(max_age_days=1 / 48) == {"Z": 2.0}  # 1h old > 30 min


def test_ccc_map_cached_network_disabled_failing_or_empty(dl, monkeypatch):
    assert dl.ccc_map_cached(allow_network=False) == {}  # no parquet, no network
    dl._CCC_CACHE = None
    fake_module(monkeypatch, "screener_in", ccc_screen=lambda: pd.DataFrame())
    assert dl.ccc_map_cached() == {} and not dl.CCC_PARQUET.exists()
    dl._CCC_CACHE = None

    def boom():
        raise RuntimeError

    fake_module(monkeypatch, "screener_in", ccc_screen=boom)
    assert dl.ccc_map_cached() == {}


def test_ccc_map_cached_without_cash_cycle_column(dl):
    _ccc_parquet(dl, {"Symbol": ["A"], "Other": [1]})
    assert dl.ccc_map_cached() == {}


def test_relpath_inside_and_outside_repo(dl, tmp_path):
    assert dl._relpath(tmp_path / "a" / "b.parquet") == str(Path("a") / "b.parquet")
    assert dl._relpath(Path("/elsewhere/x.parquet")) == "/elsewhere/x.parquet"


def test_describe(dl):
    p = dl.SEED_DIR / "cleaned_long_US.parquet"
    pd.concat([_long("A", 3), _long("B", 2, start="2024-02-01")]).to_parquet(p)
    d = dl._describe(p, "US", "stm")
    assert d["symbols"] == 2 and d["rows"] == 5 and d["start"] == "2024-01-01"
    assert d["end"] == "2024-02-02" and d["tier"] == "stm" and d["market"] == "US"
    assert d["path"] == str(Path("seed") / "cleaned_long_US.parquet")
    assert d["bytes"] == p.stat().st_size and d["sig"] == dl._sig(p)


def test_build_manifest_and_read(dl, capsys):
    assert dl.read_manifest() == {}
    _long("A").to_parquet(dl.SEED_DIR / "cleaned_long_US.parquet")
    _long("A").to_parquet(dl._ltm_path("US"))
    (dl.SEED_DIR / "cleaned_long_JP.parquet").write_text("corrupt")
    (dl.REF_DIR / "b.parquet").write_bytes(b"12")
    (dl.REF_DIR / "a.parquet").write_bytes(b"1")
    (dl.REF_DIR / "skip.csv").write_bytes(b"1")
    man = dl.build_manifest()
    assert [(a["market"], a["tier"]) for a in man["markets"]] == [("US", "stm"), ("US", "ltm")]
    assert [r["path"] for r in man["reference"]] == [str(Path("ref") / "a.parquet"), str(Path("ref") / "b.parquet")]
    out = capsys.readouterr().out
    assert "skip cleaned_long_JP.parquet" in out and "2 market assets, 2 reference" in out
    assert dl.read_manifest() == json.loads(dl.MANIFEST.read_text()) and "generated" in man
    dl.build_manifest(verbose=False)
    assert capsys.readouterr().out == ""


def test_changed_markets_and_signatures(dl):
    assert dl.changed_markets() == []
    p = dl.SEED_DIR / "cleaned_long_US.parquet"
    p.write_bytes(b"a")
    q = dl.SEED_DIR / "cleaned_long.parquet"
    q.write_bytes(b"bb")
    assert dl.changed_markets() == ["IN", "US"]  # order follows MARKETS
    dl.STORE_STATE.write_text(json.dumps(dl._stm_signatures()))
    assert dl.changed_markets() == []
    q.write_bytes(b"bbb")
    assert dl.changed_markets() == ["IN"]


def test_build_store_incremental_modes(dl, monkeypatch, tmp_path):
    pytest.importorskip("lmdb")
    monkeypatch.setenv("BHAV_CACHE", str(tmp_path / "bhav"))
    (tmp_path / "bhav").mkdir()
    store = fresh_import(monkeypatch, "bhavcopy_store")
    _long("AAA").to_parquet(dl.SEED_DIR / "cleaned_long.parquet")
    # cold: full build through the real store (it reads cleaned_long.parquet from CACHE)
    _long("AAA").to_parquet(store.CLEANED)
    r = dl.build_store_incremental(verbose=False)
    assert r["mode"] == "full" and r["symbols"] == 1 and r["changed"] == dl.MARKETS
    assert dl.STORE_STATE.exists()
    # nothing changed
    assert dl.build_store_incremental() == {"mode": "noop", "changed": []}
    # change IN seed: only IN refreshed, new symbol appears
    pd.concat([_long("AAA"), _long("BBB")]).to_parquet(dl.SEED_DIR / "cleaned_long.parquet")
    os.utime(dl.SEED_DIR / "cleaned_long.parquet", (time.time() + 9, time.time() + 9))
    r = dl.build_store_incremental(verbose=False)
    assert r == {"mode": "incremental", "symbols": 2, "changed": ["IN"]}
    assert set(store.symbols()) == {"AAA", "BBB"}
    assert dl.changed_markets() == []


def test_build_store_noop_prints_and_missing_store_forces_full(dl, monkeypatch, tmp_path, capsys):
    pytest.importorskip("lmdb")
    monkeypatch.setenv("BHAV_CACHE", str(tmp_path / "bhav"))
    (tmp_path / "bhav").mkdir()
    store = fresh_import(monkeypatch, "bhavcopy_store")
    _long("AAA").to_parquet(store.CLEANED)
    _long("AAA").to_parquet(dl.SEED_DIR / "cleaned_long.parquet")
    dl.STORE_STATE.write_text(json.dumps(dl._stm_signatures()))  # state present, store missing
    assert dl.build_store_incremental(verbose=False)["mode"] == "full"
    dl.build_store_incremental()
    assert "store up to date" in capsys.readouterr().out


def test_status_table_and_main(dl, monkeypatch, capsys):
    _long("A").to_parquet(dl.SEED_DIR / "cleaned_long_US.parquet")
    df = dl._status_table()
    assert list(df["market"]) == ["US"]
    monkeypatch.setattr("sys.argv", ["dl", "--status"])
    assert dl.main() == 0
    out = capsys.readouterr().out
    assert "US" in out and "manifest →" not in out  # --status alone reuses/creates quietly
    monkeypatch.setattr("sys.argv", ["dl"])
    dl.main()
    assert "manifest →" in capsys.readouterr().out
    called = []
    monkeypatch.setattr(dl, "build_store_incremental", lambda: called.append(1))
    monkeypatch.setattr("sys.argv", ["dl", "--store"])
    assert dl.main() == 0 and called == [1]


def test_main_status_empty(dl, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["dl", "--status"])
    assert dl.main() == 0 and capsys.readouterr().out == ""


# ---------------- serving_layer ----------------
@pytest.fixture
def sv(monkeypatch, tmp_path):
    with monkeypatch.context() as ctx:  # import creates cache_seed/serving + cdc in the repo
        ctx.setattr(Path, "mkdir", lambda *a, **k: None)
        m = fresh_import(monkeypatch, "serving_layer")
    (tmp_path / "serving").mkdir()
    (tmp_path / "cdc").mkdir()
    monkeypatch.setattr(m, "SERVING_DIR", tmp_path / "serving")
    monkeypatch.setattr(m, "CDC_DIR", tmp_path / "cdc")
    monkeypatch.setattr(m, "_SERVE_CACHE", {})
    return m


def _px(n, start=100.0, step=1.0, vol=1000.0):
    idx = pd.date_range("2023-01-01", periods=n)
    c = start + step * np.arange(n, dtype=float)
    return pd.DataFrame(
        {"Open": c, "High": c, "Low": c, "Close": c, "Volume": vol}, index=idx
    )


def test_features_short_history_none(sv):
    assert sv._features("A", "US", None) is None
    assert sv._features("A", "US", _px(19)) is None
    assert sv._features("A", "US", _px(20)) is not None


def test_features_values_on_linear_series(sv):
    d = _px(260)
    f = sv._features("A", "US", d)
    close = d["Close"]
    assert f["Symbol"] == "A" and f["Market"] == "US" and f["Bars"] == 260
    assert f["Close"] == close.iloc[-1] and f["SMA20"] == close.tail(20).mean()
    assert f["SMA50"] == close.tail(50).mean() and f["SMA200"] == close.tail(200).mean()
    assert np.isnan(f["RSI14"])  # strictly rising: zero average loss is mapped to NaN, not 100
    assert f["High252"] == close.tail(252).max() and f["Low252"] == close.tail(252).min()
    assert f["PctFromHigh"] == 0.0
    assert f["PctFromLow"] == pytest.approx((close.iloc[-1] / close.tail(252).min() - 1) * 100)
    assert f["Ret21"] == pytest.approx((close.iloc[-1] / close.iloc[-21] - 1) * 100)
    assert f["Ret252"] == pytest.approx((close.iloc[-1] / close.iloc[-252] - 1) * 100)
    assert f["Above200DMA"] is True and f["GoldenCross"] is True
    assert f["TurnoverLocal"] == pytest.approx(float((close.tail(20) * 1000).mean()))
    assert f["LastDate"] == str(d.index[-1])[:10]


def test_features_boundary_bar_counts(sv):
    f = sv._features("A", "US", _px(50))
    assert not np.isnan(f["SMA50"]) and np.isnan(f["SMA200"]) and f["Above200DMA"] is False
    assert f["GoldenCross"] is False  # needs both SMAs
    assert np.isnan(f["Ret63"]) and not np.isnan(f["Ret21"])
    f20 = sv._features("A", "US", _px(20))
    assert np.isnan(f20["SMA50"]) and not np.isnan(f20["SMA20"])
    # ret(k) needs n > k: 21 bars is not enough for the 21-bar return, 22 is
    assert np.isnan(sv._features("A", "US", _px(21))["Ret21"])
    assert not np.isnan(sv._features("A", "US", _px(22))["Ret21"])


def test_features_rsi_matches_wilder_recursion(sv):
    rng = np.random.default_rng(3)
    close = 100 + np.cumsum(rng.normal(0, 1, 80))
    d = pd.DataFrame(
        {"Close": close, "Volume": 1.0}, index=pd.date_range("2023-01-01", periods=80)
    )
    up = dn = None
    a = 1 / 14
    for i in range(1, 80):
        delta = close[i] - close[i - 1]
        u, w = max(delta, 0.0), max(-delta, 0.0)
        up = u if up is None else a * u + (1 - a) * up
        dn = w if dn is None else a * w + (1 - a) * dn
    expected = 100 - 100 / (1 + up / dn)
    assert sv._features("A", "US", d)["RSI14"] == pytest.approx(expected)


def test_features_downtrend_signals(sv):
    f = sv._features("A", "US", _px(260, start=1000.0, step=-1.0))
    assert f["RSI14"] == pytest.approx(0.0, abs=1e-6)
    assert f["Above200DMA"] is False and f["GoldenCross"] is False
    assert f["PctFromHigh"] < 0 and f["PctFromLow"] == 0.0


def test_features_zero_price_guards(sv):
    d = _px(30)
    d["Close"] = 0.0
    f = sv._features("A", "US", d)
    assert np.isnan(f["PctFromHigh"]) and np.isnan(f["PctFromLow"]) and np.isnan(f["Ret21"])


def test_source_prefers_ltm_else_seed(sv, monkeypatch, tmp_path):
    ltm = {"A": _px(30)}
    monkeypatch.setattr(sv.datalink, "load_market", lambda m, tier: ltm)
    assert sv._source("US") is ltm
    monkeypatch.setattr(sv.datalink, "load_market", lambda m, tier: {})
    monkeypatch.setattr(sv.datalink, "SEED_DIR", tmp_path)
    frames.write_seed({"S": _px(30)}, tmp_path / "cleaned_long_US.parquet")
    assert set(sv._source("US")) == {"S"}


def test_build_serving_writes_view_and_annotates(sv, monkeypatch, capsys):
    monkeypatch.setattr(sv, "_source", lambda m: {"A": _px(30), "SHORT": _px(5)})
    fake_module(monkeypatch, "liquidity", annotate=lambda df: df.assign(Liquidity="High"))
    assert sv.build_serving("US") == 1
    df = pd.read_parquet(sv.SERVING_DIR / "US.parquet")
    assert list(df["Symbol"]) == ["A"] and df["Liquidity"].iloc[0] == "High"
    assert "serving[US]: 1 symbols → US.parquet" in capsys.readouterr().out
    assert sv.build_serving("US", verbose=False) == 1 and capsys.readouterr().out == ""


def test_build_serving_empty_and_annotate_failure(sv, monkeypatch):
    monkeypatch.setattr(sv, "_source", lambda m: {"SHORT": _px(5)})
    assert sv.build_serving("US") == 0 and not (sv.SERVING_DIR / "US.parquet").exists()
    monkeypatch.setattr(sv, "_source", lambda m: {"A": _px(30)})

    def boom(df):
        raise RuntimeError

    fake_module(monkeypatch, "liquidity", annotate=boom)
    assert sv.build_serving("US", verbose=False) == 1
    assert "Liquidity" not in pd.read_parquet(sv.SERVING_DIR / "US.parquet").columns


def _view(sv, closes, market="US"):
    pd.DataFrame(
        {"Symbol": list(closes), "Close": list(closes.values()), "LastDate": "2024-01-02"}
    ).to_parquet(sv.SERVING_DIR / f"{market}.parquet")


def test_capture_deltas_insert_update_unchanged(sv, capsys):
    assert sv.capture_deltas("US") == 0  # no view yet
    _view(sv, {"A": 1.0, "B": 2.0})
    assert sv.capture_deltas("US") == 2
    log = pd.read_parquet(sv.CDC_DIR / "US.parquet")
    assert set(log["op"]) == {"INSERT"} and set(log["key"]) == {"A", "B"}
    assert "+2 deltas" in capsys.readouterr().out
    assert sv.capture_deltas("US") == 0  # nothing changed
    assert "no changes" in capsys.readouterr().out
    _view(sv, {"A": 1.0, "B": 2.5, "C": 3.0})
    assert sv.capture_deltas("US", verbose=False) == 2
    log = pd.read_parquet(sv.CDC_DIR / "US.parquet")
    new = log.iloc[2:].set_index("key")["op"].to_dict()
    assert new == {"B": "UPDATE", "C": "INSERT"} and len(log) == 4
    assert capsys.readouterr().out == ""


def test_capture_deltas_uses_latest_value_per_key(sv):
    _view(sv, {"A": 1.0})
    sv.capture_deltas("US", verbose=False)
    _view(sv, {"A": 2.0})
    sv.capture_deltas("US", verbose=False)
    _view(sv, {"A": 2.0})
    assert sv.capture_deltas("US", verbose=False) == 0  # compared against the newest logged value, not the first
    _view(sv, {"A": 1.0})
    assert sv.capture_deltas("US", verbose=False) == 1


def test_serving_read_memoised_by_signature(sv, monkeypatch):
    assert sv.serving("US").empty
    _view(sv, {"A": 1.0})
    a = sv.serving("US")
    assert sv.serving("US") is a
    p = sv.SERVING_DIR / "US.parquet"
    _view(sv, {"A": 1.0, "B": 2.0})
    os.utime(p, (time.time() + 10, time.time() + 10))
    assert len(sv.serving("US")) == 2


def _screen_view(sv):
    pd.DataFrame(
        {
            "Symbol": ["A", "B", "C", "D"],
            "RSI14": [50.0, 70.0, 30.0, 70.0],
            "Above200DMA": [True, True, False, True],
            "Ret252": [10.0, 30.0, 20.0, 5.0],
        }
    ).to_parquet(sv.SERVING_DIR / "IN.parquet")


def test_screen_fast_operators_sort_and_top(sv):
    _screen_view(sv)
    run = lambda crit, **k: list(sv.screen_fast(crit, **k)["Symbol"])  # noqa: E731
    assert run({"RSI14": ("<", 70)}) == ["C", "A"]  # sorted by Ret252 desc
    assert run({"RSI14": ("<=", 70)}) == ["B", "C", "A", "D"]
    assert run({"RSI14": (">", 50)}) == ["B", "D"]
    assert run({"RSI14": (">=", 50)}) == ["B", "A", "D"]
    assert run({"RSI14": ("==", 70.0)}) == ["B", "D"]
    assert run({"RSI14": ("!=", 70.0)}) == ["C", "A"]
    assert run({"Above200DMA": ("==", True), "RSI14": (">=", 70)}) == ["B", "D"]
    assert run({}, top=2) == ["B", "C"]
    assert run({}, top=None) == ["B", "C", "A", "D"]
    assert run({}, top=0) == ["B", "C", "A", "D"]  # falsy top = no limit
    assert run({}, sort="RSI14", top=1) in (["B"], ["D"])  # tie between B and D at 70
    assert run({}, sort="nope") == ["A", "B", "C", "D"]  # unknown sort column: original order


def test_screen_fast_ignores_bad_criteria_and_missing_view(sv):
    assert sv.screen_fast({"RSI14": ("<", 1)}).empty
    _screen_view(sv)
    assert len(sv.screen_fast({"NoCol": ("<", 1), "RSI14": ("~", 1)}, market="IN")) == 4
    assert len(sv.screen_fast({}, market="IN", top=50)) == 4


def test_refresh_only_changed_or_missing(sv, monkeypatch, capsys):
    monkeypatch.setattr(sv, "build_serving", lambda m, v: {"US": 1, "JP": 0}.get(m, 0))
    monkeypatch.setattr(sv, "capture_deltas", lambda m, v: 5)
    # explicit list: every named market rebuilt even if a view exists
    _view(sv, {"A": 1.0}, "US")
    assert sv.refresh(["US", "JP"]) == {"views": 1, "deltas": 5}
    assert "serving refresh: 1 views, 5 deltas" in capsys.readouterr().out
    # default list: only datalink-changed markets (or those lacking a view)
    monkeypatch.setattr(sv.datalink, "changed_markets", lambda: ["US"])
    built = []
    monkeypatch.setattr(sv, "build_serving", lambda m, v: built.append(m) or 1)
    sv.refresh(verbose=False)
    assert "US" in built and "IN" in built and len(built) == len(sv.MARKETS)  # all lack views except US (changed)
    built.clear()
    for m in sv.MARKETS:
        _view(sv, {"A": 1.0}, m)
    sv.refresh(verbose=False)
    assert built == ["US"]  # only the changed one


def test_serving_main(sv, monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["sv", "--screen", "IN"])
    assert sv.main() == 0
    assert "no serving view — run --refresh" in capsys.readouterr().out
    pd.DataFrame(
        {
            "Symbol": ["A", "B"],
            "Close": [1.0, 2.0],
            "RSI14": [50.0, 80.0],
            "PctFromHigh": [-1.0, -1.0],
            "Above200DMA": [True, True],
            "Ret252": [1.0, 2.0],
        }
    ).to_parquet(sv.SERVING_DIR / "IN.parquet")
    sv.main()
    out = capsys.readouterr().out
    assert "A" in out and "B" not in out.split("Symbol")[1]  # RSI < 70 filter
    seen = {}
    monkeypatch.setattr(sv, "refresh", lambda markets: seen.update(m=markets))
    monkeypatch.setattr("sys.argv", ["sv", "--refresh", "--market", "US", "JP"])
    assert sv.main() == 0 and seen == {"m": ["US", "JP"]}
