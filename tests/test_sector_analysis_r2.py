"""sector_analysis: classification cache, return loading, indices, fingerprints, clustering, CLI."""

import json

import numpy as np
import pandas as pd
import pytest

from tests.scan_loader_w4 import FakeTicker, load


@pytest.fixture
def sa(monkeypatch, tmp_path):
    mod = load(monkeypatch, "sector_analysis", tmp_path)
    monkeypatch.setattr(mod, "CACHE_DIR", tmp_path / "ohlc")
    monkeypatch.setattr(mod, "REF_DIR", tmp_path / "ref")
    monkeypatch.setattr(mod, "SECTOR_CACHE", tmp_path / "sector_map.json")
    monkeypatch.setattr(mod, "OUT_DIR", tmp_path / "out")
    (tmp_path / "ohlc").mkdir()
    (tmp_path / "ref").mkdir()
    (tmp_path / "out").mkdir()
    return mod


def _series(n=300, drift=0.001, seed=0, start="2023-01-02"):
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range(start, periods=n)
    return pd.Series(rng.normal(drift, 0.01, n), index=idx)


# ---- sector cache + fetch -------------------------------------------------------------------


def test_load_sector_cache_missing_corrupt_and_valid(sa):
    assert sa._load_sector_cache() == {}
    sa.SECTOR_CACHE.write_text("{not json")
    assert sa._load_sector_cache() == {}
    sa.SECTOR_CACHE.write_text(json.dumps({"A": {"sector": "Tech"}}))
    assert sa._load_sector_cache() == {"A": {"sector": "Tech"}}


def test_save_sector_cache_roundtrip(sa):
    sa._save_sector_cache({"A": {"sector": "X", "industry": "Y"}})
    assert json.loads(sa.SECTOR_CACHE.read_text()) == {"A": {"sector": "X", "industry": "Y"}}


def test_fetch_sector_ok_missing_fields_error_and_no_yf(sa, monkeypatch):
    FakeTicker.spec = {"info": {"sector": "Tech", "industry": "Software"}}
    monkeypatch.setattr(sa.yf, "Ticker", FakeTicker)
    assert sa.fetch_sector("AAA") == ("AAA", "Tech", "Software")
    FakeTicker.spec = {"info": {"sector": None}}
    assert sa.fetch_sector("AAA") == ("AAA", "Unknown", "Unknown")
    FakeTicker.spec = {"info": None}
    assert sa.fetch_sector("AAA") == ("AAA", "Unknown", "Unknown")
    FakeTicker.spec = {"ctor_error": "boom"}
    assert sa.fetch_sector("AAA") == ("AAA", "Unknown", "Unknown")
    monkeypatch.setattr(sa, "_YF_OK", False)
    assert sa.fetch_sector("AAA") == ("AAA", "Unknown", "Unknown")


def test_classify_sectors_fetches_only_missing_and_persists(sa, monkeypatch, capsys):
    sa._save_sector_cache({"OLD": {"sector": "Cached", "industry": "c"}})
    calls = []

    def fake_pm(fn, items, workers, progress_every, label):
        calls.append((list(items), workers, label))
        return [("NEW", "Tech", "Soft")]

    monkeypatch.setattr(sa, "parallel_map", fake_pm)
    out = sa.classify_sectors(["OLD", "NEW", "ZZZ"], workers=3)
    assert calls == [(["NEW", "ZZZ"], 3, "sectors")]
    assert out == {
        "OLD": {"sector": "Cached", "industry": "c"},
        "NEW": {"sector": "Tech", "industry": "Soft"},
    }
    assert "NEW" in json.loads(sa.SECTOR_CACHE.read_text())
    assert "3 symbols | 1 cached | 2 to fetch" in capsys.readouterr().out


def test_classify_sectors_refresh_ignores_cache_and_all_cached_skips_fetch(sa, monkeypatch):
    sa._save_sector_cache({"A": {"sector": "S", "industry": "i"}})
    monkeypatch.setattr(sa, "parallel_map", lambda *a, **k: pytest.fail("should not fetch"))
    assert sa.classify_sectors(["A"]) == {"A": {"sector": "S", "industry": "i"}}
    seen = []
    monkeypatch.setattr(
        sa, "parallel_map", lambda fn, items, **k: seen.append(list(items)) or [("A", "N", "n")]
    )
    assert sa.classify_sectors(["A"], refresh=True) == {"A": {"sector": "N", "industry": "n"}}
    assert seen == [["A"]]


# ---- load_returns ---------------------------------------------------------------------------


def _write_prices(path, n):
    close = pd.Series(np.linspace(100, 150, n))
    pd.DataFrame({"Close": close}).to_parquet(path)


def test_load_returns_min_bars_boundary_and_tail(sa):
    _write_prices(sa.CACHE_DIR / "EXACT.parquet", 252)
    _write_prices(sa.CACHE_DIR / "SHORT.parquet", 251)
    _write_prices(sa.CACHE_DIR / "LONG.parquet", 700)
    r = sa.load_returns(["EXACT", "SHORT", "LONG", "NOFILE"])
    assert set(r) == {"EXACT", "LONG"}
    assert len(r["EXACT"]) == 251
    assert len(r["LONG"]) == 504


def test_load_returns_custom_min_bars_and_ref_dir_fallback(sa):
    _write_prices(sa.REF_DIR / "REFONLY.parquet", 50)
    assert sa.load_returns(["REFONLY"]) == {}
    r = sa.load_returns(["REFONLY"], min_bars=50)
    assert list(r) == ["REFONLY"]


def test_load_returns_primary_dir_wins_and_corrupt_is_skipped(sa):
    _write_prices(sa.CACHE_DIR / "BOTH.parquet", 300)
    _write_prices(sa.REF_DIR / "BOTH.parquet", 400)
    assert len(sa.load_returns(["BOTH"])["BOTH"]) == 299
    (sa.CACHE_DIR / "BAD.parquet").write_text("not parquet")
    assert sa.load_returns(["BAD"]) == {}


# ---- build_sector_indices -------------------------------------------------------------------


def test_build_sector_indices_filters_and_averages(sa, capsys):
    idx = pd.bdate_range("2024-01-01", periods=4)
    rets = {
        "a": pd.Series([0.01, 0.02, 0.03, 0.04], index=idx),
        "b": pd.Series([0.03, 0.04, 0.05, 0.06], index=idx),
        "c": pd.Series([0.05, 0.06, 0.07, 0.08], index=idx),
        "d": pd.Series([0.1] * 4, index=idx),
        "e": pd.Series([0.1] * 4, index=idx),
    }
    smap = {
        "a": {"sector": "Tech"},
        "b": {"sector": "Tech"},
        "c": {"sector": "Tech"},
        "d": {"sector": "Small"},  # only 2 members -> dropped
        "e": {"sector": "Small"},
        "f": {"sector": "Tech"},  # no return series -> ignored
        "g": {"sector": "Unknown"},
        "h": {"sector": None},
        "i": {},  # missing sector key -> Unknown
    }
    for s in "ghi":
        rets[s] = rets["a"]
    out, groups = sa.build_sector_indices(smap, rets)
    assert groups == {"Tech": ["a", "b", "c"]}
    assert list(out.columns) == ["Tech"]
    assert out["Tech"].tolist() == pytest.approx([0.03, 0.04, 0.05, 0.06])
    assert "1 sectors with" in capsys.readouterr().out


def test_build_sector_indices_exactly_three_members_kept_two_dropped(sa):
    idx = pd.bdate_range("2024-01-01", periods=3)
    r = pd.Series([0.01, 0.0, 0.01], index=idx)
    smap = {k: {"sector": "X"} for k in "abc"}
    smap.update({k: {"sector": "Y"} for k in "de"})
    _, groups = sa.build_sector_indices(smap, {k: r for k in "abcde"})
    assert list(groups) == ["X"]


# ---- sector_fingerprint ---------------------------------------------------------------------


def test_fingerprint_too_short_returns_empty(sa):
    assert sa.sector_fingerprint(_series(99), _series(99)) == {}
    assert sa.sector_fingerprint(_series(100), _series(100)) != {}


def test_fingerprint_values_match_manual_computation(sa):
    r = _series(300, seed=1)
    m = _series(300, seed=2)
    fp = sa.sector_fingerprint(r, m)
    ann = (1 + r).prod() ** (252 / 300) - 1
    vol = r.std() * np.sqrt(252)
    cum = (1 + r).cumprod()
    dd = ((cum - cum.cummax()) / cum.cummax()).min()
    al = pd.concat([r, m], axis=1).dropna()
    beta = al.cov().iloc[0, 1] / al.iloc[:, 1].var()
    assert fp["ann_return"] == round(ann * 100, 2)
    assert fp["ann_vol"] == round(vol * 100, 2)
    assert fp["sharpe"] == round(ann / vol, 3)
    assert fp["max_drawdown"] == round(dd * 100, 2)
    assert fp["beta_to_market"] == round(beta, 3)
    assert fp["recent_3m_mom"] == round((1 + r.tail(63)).prod() * 100 - 100, 2)
    assert set(fp) == set(sa.SECTOR_FEATURES)
    assert fp["max_drawdown"] <= 0
    assert 0 <= fp["up_month_ratio"] <= 100
    assert fp["best_month"] >= fp["worst_month"]


def test_fingerprint_zero_vol_gives_zero_sharpe(sa):
    idx = pd.bdate_range("2023-01-02", periods=150)
    r = pd.Series(0.0, index=idx)
    fp = sa.sector_fingerprint(r, r)
    assert fp["ann_vol"] == 0
    assert fp["sharpe"] == 0


def test_fingerprint_beta_defaults_to_one_when_market_degenerate(sa):
    r = _series(150, seed=3)
    flat = pd.Series(0.0, index=r.index)
    assert sa.sector_fingerprint(r, flat)["beta_to_market"] == 1.0
    # fewer than 31 aligned points -> default beta
    short_mkt = _series(30, seed=4, start=str(r.index[0].date()))
    assert sa.sector_fingerprint(r, short_mkt)["beta_to_market"] == 1.0
    # 31 aligned points -> real beta (not defaulted)
    mkt31 = r.iloc[:31] * 2
    assert sa.sector_fingerprint(r, mkt31)["beta_to_market"] == pytest.approx(0.5, abs=0.01)


def test_fingerprint_beta_of_scaled_market(sa):
    r = _series(200, seed=5)
    assert sa.sector_fingerprint(r * 2, r)["beta_to_market"] == pytest.approx(2.0, abs=0.01)


# ---- cluster_sectors ------------------------------------------------------------------------


class _FakeKMeans:
    last = None

    def __init__(self, n_clusters, random_state, n_init):
        _FakeKMeans.last = (n_clusters, random_state, n_init)
        self.n = n_clusters

    def fit_predict(self, X):
        return np.arange(len(X)) % self.n


class _FakeScaler:
    def fit_transform(self, X):
        return np.asarray(X, dtype=float)


def _idx(k):
    return pd.DataFrame({f"S{i}": _series(160, seed=i) for i in range(k)})


def test_cluster_sectors_uses_requested_clusters(sa, monkeypatch):
    monkeypatch.setattr(sa, "KMeans", _FakeKMeans, raising=False)
    monkeypatch.setattr(sa, "StandardScaler", _FakeScaler, raising=False)
    fdf, market = sa.cluster_sectors(_idx(6), n_clusters=3)
    assert _FakeKMeans.last == (3, 42, 10)
    assert sorted(fdf["sector"]) == [f"S{i}" for i in range(6)]
    assert fdf["cluster"].tolist() == [0, 1, 2, 0, 1, 2]
    assert market.equals(_idx(6).mean(axis=1))


def test_cluster_sectors_reduces_when_fewer_sectors_than_clusters(sa, monkeypatch):
    monkeypatch.setattr(sa, "KMeans", _FakeKMeans, raising=False)
    monkeypatch.setattr(sa, "StandardScaler", _FakeScaler, raising=False)
    sa.cluster_sectors(_idx(3), n_clusters=4)
    assert _FakeKMeans.last[0] == 2  # max(2, 3 // 2)
    sa.cluster_sectors(_idx(4), n_clusters=4)  # exactly enough: unchanged
    assert _FakeKMeans.last[0] == 4


def test_cluster_sectors_skips_short_series(sa, monkeypatch):
    monkeypatch.setattr(sa, "KMeans", _FakeKMeans, raising=False)
    monkeypatch.setattr(sa, "StandardScaler", _FakeScaler, raising=False)
    idx = _idx(4)
    idx.iloc[:70, 0] = np.nan  # leaves only 90 observations (<100)
    fdf, _ = sa.cluster_sectors(idx, n_clusters=2)
    assert "S0" not in fdf["sector"].tolist()


# ---- name_sector_cluster --------------------------------------------------------------------


def _prof(ret, vol, beta):
    return pd.Series({"ann_return": ret, "ann_vol": vol, "beta_to_market": beta})


@pytest.mark.parametrize(
    "args,needle",
    [
        ((30, 40, 1), "High-Growth"),
        ((25, 40, 1), "Market-Tracking"),  # ret > 25 is strict
        ((30, 35, 1), "Quality"),  # vol > 35 strict -> falls to quality? vol<30 false
        ((20, 29, 1), "Quality"),
        ((15, 29, 1), "Market-Tracking"),  # ret > 15 strict
        ((20, 30, 1), "Market-Tracking"),  # vol < 30 strict
        ((5, 40, 1.3), "High-Beta"),
        ((5, 40, 1.2), "Market-Tracking"),
        ((5, 40, 0.6), "Defensive"),
        ((5, 40, 0.7), "Market-Tracking"),
        ((-1, 40, 1.0), "Laggards"),
        ((0, 40, 1.0), "Market-Tracking"),
    ],
)
def test_name_sector_cluster_branches(sa, args, needle):
    if args == (30, 35, 1):
        needle = "Market-Tracking"
    assert needle in sa.name_sector_cluster(_prof(*args))


# ---- extract_patterns / save_excel / universe / main ---------------------------------------


def _fdf(k=6):
    rows = []
    for i in range(k):
        rows.append(
            {
                "sector": f"S{i}",
                "ann_return": 10 + i,
                "ann_vol": 20.0,
                "sharpe": 0.5 + i / 10,
                "max_drawdown": -10.0,
                "beta_to_market": 1.0,
                "trend_persistence": 1.0,
                "best_month": 5.0,
                "worst_month": -5.0,
                "up_month_ratio": 55.0,
                "recent_3m_mom": float(i),
                "cluster": i % 2,
            }
        )
    return pd.DataFrame(rows)


def test_extract_patterns_prints_sections_and_returns_corr(sa, capsys):
    idx = _idx(6)
    corr = sa.extract_patterns(_fdf(), idx, idx.mean(axis=1))
    out = capsys.readouterr().out
    assert corr.shape == (6, 6)
    assert "SECTOR RANKING" in out and "SECTOR ROTATION" in out
    # rotation leaders are the highest 3m momentum, laggards the lowest
    assert "Leaders:  S5 (+5%), S4 (+4%), S3 (+3%)" in out
    assert "Laggards: S2 (+2%), S1 (+1%), S0 (+0%)" in out
    # ranking printed best Sharpe first
    assert out.index("S5 ") < out.index("S0 ", out.index("SECTOR RANKING"))
    assert out.count("ρ=") == 10


def test_save_excel_writes_four_sheets(sa, monkeypatch, tmp_path):
    written = {}

    class FakeWriter:
        def __init__(self, path, engine):
            written["path"], written["engine"] = path, engine

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(sa.pd, "ExcelWriter", FakeWriter)
    sheets = []
    monkeypatch.setattr(
        pd.DataFrame, "to_excel", lambda self, w, name, **k: sheets.append((name, len(self)))
    )
    idx = _idx(3)
    path = sa.save_excel(_fdf(3), idx, idx.corr(), {"A": ["x"] * 50, "B": ["y"]}, "IN")
    assert written["engine"] == "openpyxl"
    assert path.parent == sa.OUT_DIR
    assert path.name.startswith("sector_analysis_IN_") and path.suffix == ".xlsx"
    assert [s[0] for s in sheets] == [
        "DISCLAIMER",
        "Sector_Fingerprints",
        "Sector_Correlation",
        "Sector_Members",
    ]


def test_save_excel_members_truncated_to_40_and_sorted(sa, monkeypatch):
    class W:
        def __init__(self, *a, **k):
            pass

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

    monkeypatch.setattr(sa.pd, "ExcelWriter", W)
    captured = {}
    monkeypatch.setattr(
        pd.DataFrame, "to_excel", lambda self, w, name, **k: captured.setdefault(name, self)
    )
    idx = _idx(3)
    sa.save_excel(
        _fdf(3), idx, idx.corr(), {"small": ["a"], "big": [str(i) for i in range(50)]}, "US"
    )
    mem = captured["Sector_Members"]
    assert mem["Sector"].tolist() == ["big", "small"]
    assert len(mem.iloc[0]["Members"].split(", ")) == 40
    sf = captured["Sector_Fingerprints"]
    assert sf["sharpe"].is_monotonic_decreasing


def test_load_universe_market_filters_and_max(sa):
    for n in ["AAA.NS", "BBB.NS", "CCC.BO", "DDD", "EEE"]:
        (sa.CACHE_DIR / f"{n}.parquet").write_text("")
    (sa.REF_DIR / "AAA.NS.parquet").write_text("")  # duplicate across dirs
    (sa.REF_DIR / "FFF.parquet").write_text("")
    assert sa.load_universe("IN", 0) == ["AAA.NS", "BBB.NS"]
    assert sa.load_universe("US", 0) == ["DDD", "EEE", "FFF"]
    assert sa.load_universe("ALL", 0) == [
        "AAA.NS",
        "BBB.NS",
        "CCC.BO",
        "DDD",
        "EEE",
        "FFF",
    ]
    assert sa.load_universe("ALL", 2) == ["AAA.NS", "BBB.NS"]


def test_main_aborts_without_dependencies(sa, monkeypatch, capsys):
    monkeypatch.setattr(sa, "_SK_OK", False)
    monkeypatch.setattr(sa, "load_universe", lambda *a: pytest.fail("not reached"))
    assert sa.main() is None
    assert "pip install scikit-learn yfinance" in capsys.readouterr().out
    monkeypatch.setattr(sa, "_SK_OK", True)
    monkeypatch.setattr(sa, "_YF_OK", False)
    sa.main()
    assert "pip install" in capsys.readouterr().out


def test_main_stops_when_too_few_sectors(sa, monkeypatch, capsys):
    monkeypatch.setattr(sa, "_SK_OK", True)
    monkeypatch.setattr(sa, "load_universe", lambda m, mx: ["A"])
    monkeypatch.setattr(sa, "classify_sectors", lambda s, refresh=False: {"A": {"sector": "X"}})
    monkeypatch.setattr(sa, "load_returns", lambda s: {})
    monkeypatch.setattr(
        sa, "build_sector_indices", lambda m, r: (pd.DataFrame({"a": [1], "b": [2]}), {})
    )
    monkeypatch.setattr(sa, "cluster_sectors", lambda *a: pytest.fail("not reached"))
    sa.main()
    assert "Not enough sectors with data." in capsys.readouterr().out


def test_main_full_pipeline_passes_args(sa, monkeypatch):
    monkeypatch.setattr(sa, "_SK_OK", True)
    seen = {}
    monkeypatch.setattr(sa, "load_universe", lambda m, mx: seen.update(u=(m, mx)) or ["A", "B"])

    def cls(s, refresh=False):
        seen["refresh"] = refresh
        return {"A": {"sector": "X"}}

    monkeypatch.setattr(sa, "classify_sectors", cls)
    monkeypatch.setattr(sa, "load_returns", lambda s: seen.update(ret=list(s)) or {})
    idx = pd.DataFrame({"a": [1], "b": [2], "c": [3]})
    monkeypatch.setattr(sa, "build_sector_indices", lambda m, r: (idx, {"g": ["A"]}))
    monkeypatch.setattr(sa, "cluster_sectors", lambda i, n: seen.update(n=n) or ("fdf", "mkt"))
    monkeypatch.setattr(sa, "extract_patterns", lambda f, i, m: ("corr", f, m))
    monkeypatch.setattr(sa, "save_excel", lambda *a: seen.update(save=a))
    sa.main(market="US", n_clusters=7, max_stocks=5, refresh=True)
    assert seen["u"] == ("US", 5)
    assert seen["refresh"] is True
    assert seen["ret"] == ["A"]
    assert seen["n"] == 7
    assert seen["save"][0] == "fdf" and seen["save"][-1] == "US"
    assert seen["save"][2][0] == "corr"
