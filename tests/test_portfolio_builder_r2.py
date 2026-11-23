"""portfolio_builder: pick loading, parquet price loading, risk metrics, MPT optimiser, build()."""

import types

import numpy as np
import pandas as pd
import pytest

from tests import r2_fakes
from tests.w4_helpers import fresh_import


class FakeMinimize:
    """scipy.optimize.minimize stand-in: records calls; `plan[kind]` is x, a callable, or None=fail."""

    def __init__(self):
        self.calls = []
        self.plan = {}
        self.seen_sharpe = 0

    def kind_of(self, constraints):
        if constraints[-1]["type"] == "ineq":
            return "target"
        if len(constraints) == 2:
            return "frontier"
        self.seen_sharpe += 1
        return "sharpe" if self.seen_sharpe == 1 else "minvar"

    def __call__(self, fun, x0, method=None, bounds=None, constraints=None):
        kind = self.kind_of(constraints)
        self.calls.append(
            types.SimpleNamespace(
                kind=kind,
                fun=fun,
                x0=np.asarray(x0),
                method=method,
                bounds=bounds,
                cons=constraints,
            )
        )
        x = self.plan.get(kind, "default")
        if isinstance(x, str):
            x = np.asarray(x0, dtype=float)
        if callable(x):
            x = x(len(self.calls), np.asarray(x0, dtype=float))
        if x is None:
            return types.SimpleNamespace(x=np.asarray(x0, dtype=float), success=False)
        return types.SimpleNamespace(x=np.asarray(x, dtype=float), success=True)

    def of(self, kind):
        return [c for c in self.calls if c.kind == kind]


@pytest.fixture
def pb(monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    fm = FakeMinimize()
    r2_fakes.install_scipy_minimize(monkeypatch, fm)
    mod = fresh_import(monkeypatch, "portfolio_builder")
    for name, sub in (
        ("CACHE_DIR", "cache"),
        ("REF_DIR", "ref"),
        ("INDEX_DIR", "index"),
        ("OUT_DIR", "out"),
    ):
        monkeypatch.setattr(mod, name, tmp_path / sub)
        (tmp_path / sub).mkdir()
    mod.fm = fm
    return mod


def prices(seed, n=600, drift=0.0005, start="2022-01-03", tz=None):
    rng = np.random.default_rng(seed)
    c = 100 * np.exp(np.cumsum(rng.normal(drift, 0.01, n)))
    idx = pd.date_range(start, periods=n, freq="B", tz=tz)
    return pd.DataFrame({"Close": c}, index=idx)


def rets_frame(n=300, k=4, seed=1):
    rng = np.random.default_rng(seed)
    idx = pd.date_range("2023-01-02", periods=n, freq="B")
    return pd.DataFrame(
        rng.normal(0.0006, 0.01, (n, k)), index=idx, columns=[f"S{i}" for i in range(k)]
    )


# ---------------------------------------------------------------- load_picks


class FakeWorkbook:
    """Replaces pd.ExcelFile / pd.read_excel so no openpyxl is needed."""

    def __init__(self, monkeypatch, sheets):
        self.sheets = sheets
        monkeypatch.setattr(
            pd, "ExcelFile", lambda f: types.SimpleNamespace(sheet_names=list(sheets))
        )
        monkeypatch.setattr(pd, "read_excel", lambda f, sheet_name=None: sheets[sheet_name])


def _scan_file(tmp_path, folder, name="x_full_scan_20260101.xlsx"):
    d = tmp_path / folder
    d.mkdir(exist_ok=True)
    (d / name).write_bytes(b"")
    return d / name


def test_load_picks_no_files(pb):
    assert pb.load_picks("IN") == {"triple": [], "coffee": [], "other": []}
    assert pb.load_picks("US") == {"triple": [], "coffee": [], "other": []}


def test_load_picks_uses_last_file_and_market_dir(pb, monkeypatch, tmp_path):
    _scan_file(tmp_path, "indian_full_scan", "a_full_scan_20250101.xlsx")
    _scan_file(tmp_path, "indian_full_scan", "a_full_scan_20260101.xlsx")
    seen = []
    monkeypatch.setattr(
        pd,
        "ExcelFile",
        lambda f: seen.append(f) or types.SimpleNamespace(sheet_names=[]),
    )
    pb.load_picks("IN")
    assert seen[0].endswith("a_full_scan_20260101.xlsx")
    seen.clear()
    _scan_file(tmp_path, "us_full_scan", "u_full_scan_20260101.xlsx")
    pb.load_picks("US")
    assert seen[0].startswith("us_full_scan/")
    seen.clear()
    pb.load_picks("anything-else")  # non-IN -> US dir
    assert seen[0].startswith("us_full_scan/")


def test_load_picks_triple_coffee_other_with_dedup(pb, monkeypatch, tmp_path):
    _scan_file(tmp_path, "indian_full_scan")
    FakeWorkbook(
        monkeypatch,
        {
            "Triple_Hits": pd.DataFrame({"Symbol": [" AAA ", "", "BBB", "   "]}),
            "Coffee_Can": pd.DataFrame({"Symbol": ["CCC", "AAA"]}),
            "Multi_Screen_Hits": pd.DataFrame({"Symbol": ["AAA", "DDD", "CCC", "EEE"]}),
            "Piotroski_Strong": pd.DataFrame({"Symbol": ["EEE", "FFF", ""]}),
            "Magic_Formula": pd.DataFrame({"Symbol": ["GGG"]}),
            "Bull_Cartel": pd.DataFrame({"Symbol": ["HHH"]}),
            "Fundamentals": pd.DataFrame(
                {
                    "Symbol": ["III", "JJJ", "KKK", "DDD"],
                    "CoffeeCan": ["PASS", "fail", "pass", "PASS"],
                    "Piotroski_Strong": ["yes", "YES", "no", "YES"],
                }
            ),
        },
    )
    out = pb.load_picks("IN")
    assert out["triple"] == ["AAA", "BBB"]
    assert out["coffee"] == ["CCC", "AAA"]  # Coffee_Can sheet wins; Fundamentals ignored for it
    # sheets in fixed order; Fundamentals rows need Piotroski_Strong == YES (case-insensitive)
    assert out["other"] == ["DDD", "EEE", "FFF", "GGG", "HHH", "III", "JJJ"]


def test_load_picks_coffee_fallback_to_fundamentals(pb, monkeypatch, tmp_path):
    _scan_file(tmp_path, "indian_full_scan")
    FakeWorkbook(
        monkeypatch,
        {
            "Fundamentals": pd.DataFrame(
                {
                    "Symbol": ["AAA", "BBB", "CCC", " "],
                    "CoffeeCan": ["PASS", "FAIL", "pass", "PASS"],
                    "Piotroski_Strong": ["NO", "YES", "NO", "YES"],
                }
            )
        },
    )
    out = pb.load_picks("IN")
    assert out["triple"] == []
    assert out["coffee"] == ["AAA", "CCC"]
    assert out["other"] == ["BBB"]  # AAA/CCC already seen; BBB strong


def test_load_picks_fundamentals_without_flags(pb, monkeypatch, tmp_path):
    _scan_file(tmp_path, "indian_full_scan")
    FakeWorkbook(monkeypatch, {"Fundamentals": pd.DataFrame({"Symbol": ["AAA"]})})
    assert pb.load_picks("IN") == {"triple": [], "coffee": [], "other": []}


# ---------------------------------------------------------------- index / price loading


def test_norm_index_tz_normalise_and_dedupe(pb):
    idx = pd.DatetimeIndex(
        ["2024-01-02 09:15", "2024-01-02 15:30", "2024-01-03 10:00"], tz="Asia/Kolkata"
    )
    s = pd.Series([1.0, 2.0, 3.0], index=idx)
    out = pb._norm_index(s)
    assert out.index.tz is None
    assert list(out.index) == [pd.Timestamp("2024-01-02"), pd.Timestamp("2024-01-03")]
    assert list(out.values) == [2.0, 3.0]  # keep="last"
    assert list(s.values) == [1.0, 2.0, 3.0]  # input untouched


def test_norm_index_accepts_string_index(pb):
    s = pd.Series([1.0, 2.0], index=["2024-01-02", "2024-01-03"])
    assert isinstance(pb._norm_index(s).index, pd.DatetimeIndex)


def test_load_ohlc_suffix_order_and_dirs(pb):
    prices(1, 10).to_parquet(pb.CACHE_DIR / "AAA.parquet")
    a_ns = prices(2, 10)
    a_ns.to_parquet(pb.CACHE_DIR / "AAA.NS.parquet")
    prices(3, 10).to_parquet(pb.REF_DIR / "BBB.NS.parquet")
    # India: .NS file preferred over bare
    got = pb._load_ohlc("AAA", "IN")
    assert got.iloc[0] == pytest.approx(a_ns["Close"].iloc[0])
    # US: bare name only, no suffix tried first
    got_us = pb._load_ohlc("AAA", "US")
    assert got_us.iloc[0] == pytest.approx(prices(1, 10)["Close"].iloc[0])
    # falls through to REF_DIR
    assert len(pb._load_ohlc("BBB", "IN")) == 10
    assert pb._load_ohlc("BBB", "US").empty  # US wants BBB.parquet, only BBB.NS exists
    assert pb._load_ohlc("NOPE", "IN").empty
    assert pb._load_ohlc("NOPE", "IN").dtype == float


def test_load_ohlc_corrupt_file_falls_through(pb):
    (pb.CACHE_DIR / "X.NS.parquet").write_bytes(b"junk")
    prices(4, 10).to_parquet(pb.REF_DIR / "X.NS.parquet")
    assert len(pb._load_ohlc("X", "IN")) == 10
    prices(4, 10).drop(columns="Close").assign(Other=1.0).to_parquet(pb.CACHE_DIR / "Y.parquet")
    assert pb._load_ohlc("Y", "US").empty  # missing Close column is swallowed


def test_load_returns_bar_threshold_and_lookback(pb):
    prices(1, 250).to_parquet(pb.CACHE_DIR / "OK.NS.parquet")  # exactly 250 bars -> kept
    prices(2, 249).to_parquet(pb.CACHE_DIR / "SHORT.NS.parquet")
    r = pb.load_returns(["OK", "SHORT", "MISSING"], "IN")
    assert set(r.columns) == {"OK"}
    assert r["OK"].notna().sum() == 249  # pct_change drops the first bar
    prices(3, 700).to_parquet(pb.CACHE_DIR / "LONG.NS.parquet")
    r2 = pb.load_returns(["LONG"], "IN")
    assert len(r2) == pb.LOOKBACK


def test_load_returns_drops_sparse_columns_at_90pct(pb):
    prices(1, 700).to_parquet(pb.CACHE_DIR / "FULL.NS.parquet")
    last = pd.date_range("2022-01-03", periods=700, freq="B")[-1]
    # 453 returns of 504 -> 89.9% -> dropped; 454 -> 90.08% -> kept
    for name, n in (("DROP", 454), ("KEEP", 455)):
        df = prices(5, n, start=None) if False else prices(5, n)
        df.index = pd.date_range(end=last, periods=n, freq="B")
        df.to_parquet(pb.CACHE_DIR / f"{name}.NS.parquet")
    r = pb.load_returns(["FULL", "DROP", "KEEP"], "IN")
    assert "KEEP" in r.columns and "DROP" not in r.columns


def test_load_returns_empty(pb):
    out = pb.load_returns(["NOPE"], "IN")
    assert isinstance(out, pd.DataFrame) and out.empty


def test_load_index_returns(pb):
    assert pb.load_index_returns("IN").empty
    prices(1, 700).to_parquet(pb.INDEX_DIR / "NSEI.parquet")
    prices(2, 300).to_parquet(pb.INDEX_DIR / "GSPC.parquet")
    nsei = pb.load_index_returns("IN")
    assert len(nsei) == pb.LOOKBACK
    assert len(pb.load_index_returns("US")) == 299  # GSPC for non-IN
    assert nsei.index.is_monotonic_increasing


# ---------------------------------------------------------------- risk_metrics


def test_risk_metrics_values(pb):
    rng = np.random.default_rng(7)
    idx = pd.date_range("2023-01-02", periods=200, freq="B")
    mkt = pd.Series(rng.normal(0.0005, 0.01, 200), index=idx)
    rets = pd.DataFrame({"TWICE": 2 * mkt, "FLAT": pd.Series(0.0, index=idx)}, index=idx)
    rm = pb.risk_metrics(rets, mkt, 0.05).set_index("Symbol")
    t = rm.loc["TWICE"]
    ann_m = mkt.mean() * 252
    ann_r = 2 * ann_m
    assert t["Beta"] == pytest.approx(2.0, abs=1e-3)
    assert t["Corr_Nifty"] == pytest.approx(1.0, abs=1e-3)
    assert t["Ann_Return%"] == pytest.approx(round(ann_r * 100, 2))
    assert t["Ann_Vol%"] == pytest.approx(round(2 * mkt.std() * np.sqrt(252) * 100, 2))
    assert t["Alpha%"] == pytest.approx(round((ann_r - (0.05 + 2 * (ann_m - 0.05))) * 100, 2))
    assert t["Sharpe"] == pytest.approx(round((ann_r - 0.05) / (2 * mkt.std() * np.sqrt(252)), 3))
    f = rm.loc["FLAT"]
    assert f["Sharpe"] == 0 and f["Ann_Vol%"] == 0  # zero vol -> sharpe forced to 0


def test_risk_metrics_overlap_boundary_and_nan_handling(pb):
    idx = pd.date_range("2023-01-02", periods=100, freq="B")
    rng = np.random.default_rng(3)
    mkt = pd.Series(rng.normal(0, 0.01, 100), index=idx)
    r60 = pd.Series(rng.normal(0, 0.01, 100), index=idx)
    r60.iloc[60:] = np.nan  # exactly 60 overlapping rows -> kept
    r59 = pd.Series(rng.normal(0, 0.01, 100), index=idx)
    r59.iloc[59:] = np.nan  # 59 rows -> skipped
    rm = pb.risk_metrics(pd.DataFrame({"R60": r60, "R59": r59}), mkt, 0.0)
    assert list(rm["Symbol"]) == ["R60"]


def test_risk_metrics_zero_market_variance_gives_nan_beta(pb):
    idx = pd.date_range("2023-01-02", periods=100, freq="B")
    mkt = pd.Series(0.0, index=idx)
    rets = pd.DataFrame({"A": np.random.default_rng(1).normal(0, 0.01, 100)}, index=idx)
    rm = pb.risk_metrics(rets, mkt, 0.0)
    assert np.isnan(rm["Beta"].iloc[0]) and np.isnan(rm["Alpha%"].iloc[0])


def test_risk_metrics_empty(pb):
    idx = pd.date_range("2023-01-02", periods=10, freq="B")
    out = pb.risk_metrics(pd.DataFrame({"A": range(10)}, index=idx), pd.Series(0.0, index=idx), 0.0)
    assert out.empty


# ---------------------------------------------------------------- _port_perf / _expected_returns


def test_port_perf(pb):
    w = np.array([0.5, 0.5])
    mu = np.array([0.10, 0.20])
    cov = np.array([[0.04, 0.0], [0.0, 0.09]])
    r, v, s = pb._port_perf(w, mu, cov, 0.05)
    assert r == pytest.approx(0.15)
    assert v == pytest.approx(np.sqrt(0.25 * 0.04 + 0.25 * 0.09))
    assert s == pytest.approx((0.15 - 0.05) / v)
    assert pb._port_perf(w, mu, np.zeros((2, 2)), 0.05)[1:] == (0.0, 0)


def test_expected_returns_shrinkage(pb):
    R = pd.DataFrame({"A": [0.001] * 10, "B": [0.003] * 10})
    hist = np.array([0.252, 0.756])
    assert pb._expected_returns(R, 0.0) == pytest.approx(hist)
    assert pb._expected_returns(R, 1.0) == pytest.approx([hist.mean()] * 2)
    assert pb._expected_returns(R) == pytest.approx(0.5 * hist + 0.5 * hist.mean())
    assert pb._expected_returns(R, 0.25) == pytest.approx(0.75 * hist + 0.25 * hist.mean())


# ---------------------------------------------------------------- optimise


def test_optimise_insufficient_inputs(pb):
    assert pb.optimise(rets_frame(k=1), 0.05) == {}


def test_optimise_row_boundary(pb):
    assert pb.optimise(rets_frame(n=59, k=3), 0.05) == {}
    assert pb.optimise(rets_frame(n=60, k=3), 0.05) != {}


def test_optimise_drops_sparse_columns_and_nans(pb):
    R = rets_frame(n=100, k=4)
    R.iloc[:15, 0] = np.nan  # 85% non-null -> dropped
    R.iloc[:5, 1] = np.nan  # 95% -> kept, but rows with NaN dropped
    out = pb.optimise(R, 0.05)
    assert set(pb.fm.of("sharpe")[0].x0) == {1 / 3}
    assert len(pb.fm.of("sharpe")[0].bounds) == 3
    assert out["max_weight"] == pytest.approx(1 / 3 + 1e-9)  # 0.15 cap < 1/n -> raised


def test_optimise_structure_and_call_plan(pb):
    R = rets_frame(n=300, k=20)
    out = pb.optimise(R, 0.05, max_weight=0.1)
    assert out["exp_return_method"] == "shrinkage λ=0.5"
    assert out["max_weight"] == 0.1
    assert len(pb.fm.of("sharpe")) == 1 and len(pb.fm.of("minvar")) == 1
    assert len(pb.fm.of("frontier")) == 25 and "target" not in out
    c = pb.fm.of("sharpe")[0]
    assert c.method == "SLSQP"
    assert c.bounds == tuple((0, 0.1) for _ in range(20))
    assert c.cons[0]["type"] == "eq" and c.cons[0]["fun"](np.full(20, 0.06)) == pytest.approx(0.2)
    assert len(out["frontier"]) == 25
    assert set(out["frontier"].columns) == {"Target_Return%", "Volatility%", "Sharpe"}
    # equal-weight result is reported for both portfolios
    ms = out["max_sharpe"]
    assert set(ms) == {"weights", "ret", "vol", "sharpe"}
    assert list(ms["weights"].columns) == ["Symbol", "Weight%"]
    assert (ms["weights"]["Weight%"] == 5.0).all() and len(ms["weights"]) == 20


def test_optimise_objectives(pb):
    R = rets_frame(n=300, k=5, seed=4)
    pb.optimise(R, 0.05)
    mu = pb._expected_returns(R)
    cov = R.cov().values * 252
    w = np.array([0.4, 0.3, 0.1, 0.1, 0.1])
    assert pb.fm.of("sharpe")[0].fun(w) == pytest.approx(-pb._port_perf(w, mu, cov, 0.05)[2])
    assert pb.fm.of("minvar")[0].fun(w) == pytest.approx(w @ cov @ w)
    tr = np.linspace(mu.min(), mu.max(), 25)
    fr = pb.fm.of("frontier")
    assert fr[0].cons[1]["fun"](w) == pytest.approx(w @ mu - tr[0])
    assert fr[24].cons[1]["fun"](w) == pytest.approx(w @ mu - tr[24])
    assert fr[3].cons[1]["type"] == "eq"  # each frontier point binds its own target (late-binding)
    assert fr[3].cons[1]["fun"](w) == pytest.approx(w @ mu - tr[3])


def test_optimise_cap_floor_is_one_over_n(pb):
    R = rets_frame(n=300, k=5)
    out = pb.optimise(R, 0.05, max_weight=0.05)  # below 1/n -> raised
    assert out["max_weight"] == pytest.approx(0.2 + 1e-9)
    assert pb.fm.of("sharpe")[0].bounds[0] == (0, pytest.approx(0.2 + 1e-9))
    pb.fm.calls.clear()
    pb.fm.seen_sharpe = 0
    out2 = pb.optimise(R, 0.05, max_weight=0.5)
    assert out2["max_weight"] == 0.5


def test_optimise_frontier_skips_failed_solves(pb):
    pb.fm.plan["frontier"] = lambda call_no, x0: None if call_no % 2 == 0 else x0
    out = pb.optimise(rets_frame(k=5), 0.05)
    assert 0 < len(out["frontier"]) < 25
    pb.fm.plan["frontier"] = None
    pb.fm.calls.clear()
    pb.fm.seen_sharpe = 0
    assert pb.optimise(rets_frame(k=5), 0.05)["frontier"].empty


def test_optimise_weight_table_threshold_and_order(pb):
    R = rets_frame(n=300, k=4)
    pb.fm.plan["sharpe"] = [0.005, 0.2, 0.7945, 0.0005]  # 0.5% is NOT > 0.5
    pb.fm.plan["minvar"] = [0.25, 0.25, 0.25, 0.25]
    out = pb.optimise(R, 0.05)
    w = out["max_sharpe"]["weights"]
    assert list(w["Symbol"]) == ["S2", "S1"]
    assert list(w["Weight%"]) == [79.45, 20.0]
    assert len(out["min_var"]["weights"]) == 4


def test_optimise_performance_numbers_in_percent(pb):
    R = rets_frame(n=300, k=4)
    pb.fm.plan["sharpe"] = [1.0, 0.0, 0.0, 0.0]
    out = pb.optimise(R, 0.05, max_weight=1.0)
    mu = pb._expected_returns(R)
    cov = R.cov().values * 252
    r, v, s = pb._port_perf(np.array([1.0, 0, 0, 0]), mu, cov, 0.05)
    assert out["max_sharpe"]["ret"] == pytest.approx(r * 100)
    assert out["max_sharpe"]["vol"] == pytest.approx(v * 100)
    assert out["max_sharpe"]["sharpe"] == pytest.approx(s)


def test_optimise_shrink_forwarded(pb):
    R = rets_frame(n=300, k=4)
    out = pb.optimise(R, 0.05, shrink=1.0)
    assert out["exp_return_method"] == "shrinkage λ=1.0"
    mu = pb._expected_returns(R, 1.0)
    fr = pb.fm.of("frontier")
    assert fr[0].cons[1]["fun"](np.zeros(4)) == pytest.approx(-mu.min())


def test_optimise_target_feasible(pb):
    R = rets_frame(n=300, k=4, seed=2)
    mu = pb._expected_returns(R)
    pb.fm.plan["target"] = [0.25, 0.25, 0.25, 0.25]
    out = pb.optimise(R, 0.05, max_weight=0.5, target_return=0.05)
    t = out["target"]
    order = np.argsort(mu)[::-1]
    w_max = np.zeros(4)
    w_max[order[0]] = 0.5
    w_max[order[1]] = 0.5
    assert t["max_achievable%"] == pytest.approx(round(float(w_max @ mu) * 100, 2))
    assert t["requested%"] == pytest.approx(5.0)
    assert t["feasible"] is True
    call = pb.fm.of("target")[0]
    assert call.cons[-1]["fun"](w_max) == pytest.approx(float(w_max @ mu) - 0.05)
    assert len(t["weights"]) == 4  # solver result used


def test_optimise_target_capped_to_max_achievable(pb):
    R = rets_frame(n=300, k=4, seed=2)
    mu = pb._expected_returns(R)
    out = pb.optimise(R, 0.05, max_weight=0.5, target_return=10.0)
    t = out["target"]
    assert t["feasible"] is False
    call = pb.fm.of("target")[0]
    top2 = np.sort(mu)[::-1][:2]
    max_ach = 0.5 * top2.sum()
    # the inequality is bound to the *capped* target
    assert call.cons[-1]["fun"](np.zeros(4)) == pytest.approx(-max_ach)
    assert t["max_achievable%"] == pytest.approx(round(max_ach * 100, 2))
    assert t["requested%"] == pytest.approx(1000.0)


def test_optimise_target_feasible_boundary_equal(pb):
    R = rets_frame(n=300, k=4, seed=2)
    mu = pb._expected_returns(R)
    max_ach = 0.5 * np.sort(mu)[::-1][:2].sum()
    out = pb.optimise(R, 0.05, max_weight=0.5, target_return=float(max_ach))
    assert out["target"]["feasible"] is True


def test_optimise_target_solver_failure_uses_greedy_weights(pb):
    R = rets_frame(n=300, k=4, seed=2)
    pb.fm.plan["target"] = None
    out = pb.optimise(R, 0.05, max_weight=0.5, target_return=0.05)
    w = out["target"]["weights"]
    assert len(w) == 2 and list(w["Weight%"]) == [50.0, 50.0]
    mu = pb._expected_returns(R)
    top2 = set(np.argsort(mu)[::-1][:2])
    assert {int(s[1:]) for s in w["Symbol"]} == top2


def test_optimise_target_greedy_with_partial_remainder(pb):
    R = rets_frame(n=300, k=5, seed=6)
    pb.fm.plan["target"] = None
    out = pb.optimise(R, 0.05, max_weight=0.4, target_return=0.01)
    w = sorted(out["target"]["weights"]["Weight%"], reverse=True)
    assert w == [40.0, 40.0, 20.0]  # 0.4 + 0.4 + remaining 0.2


# ---------------------------------------------------------------- build


def _setup_build(pb, monkeypatch, tmp_path, n_stocks=5, with_index=True):
    for i in range(n_stocks):
        prices(10 + i, 600).to_parquet(pb.CACHE_DIR / f"S{i}.NS.parquet")
    if with_index:
        prices(99, 600, drift=0.0004).to_parquet(pb.INDEX_DIR / "NSEI.parquet")
    _scan_file(tmp_path, "indian_full_scan")
    FakeWorkbook(
        monkeypatch,
        {
            "Triple_Hits": pd.DataFrame({"Symbol": ["S0", "S1"]}),
            "Coffee_Can": pd.DataFrame({"Symbol": ["S2", "S0"]}),
            "Multi_Screen_Hits": pd.DataFrame({"Symbol": ["S3", "S4", "GHOST"]}),
        },
    )
    return r2_fakes.FakeExcel().install(monkeypatch)


def test_build_end_to_end(pb, monkeypatch, tmp_path, capsys):
    xl = _setup_build(pb, monkeypatch, tmp_path)
    pb.build("IN", 25, 0.065, max_weight=0.5, target_return=0.2)
    out = capsys.readouterr().out
    assert "Picks: 2 triple, 2 coffee can, 3 other" in out
    assert "Optimisation universe: 6 holdings (cap 25)" in out  # deduped, GHOST has no data
    assert "Loaded returns for 5 stocks" in out
    w = xl.writers[0]
    assert w.path.parent == pb.OUT_DIR and w.path.name.startswith("portfolio_IN_")
    assert list(w.sheets) == [
        "1_Triple_Hits", "2_Coffee_Can", "3_Other_Picks", "4_Risk_Metrics",
        "5_Efficient_Frontier", "6_MaxSharpe_Weights", "7_MinVar_Weights",
        "8_Target_Return_Weights", "9_Portfolio_Summary", "DISCLAIMER",
    ]  # fmt: skip
    assert set(w.sheets["1_Triple_Hits"]["Symbol"]) == {"S0", "S1"}
    assert set(w.sheets["3_Other_Picks"]["Symbol"]) == {"S3", "S4"}
    assert w.sheets["4_Risk_Metrics"]["Sharpe"].is_monotonic_decreasing
    fr = w.sheets["5_Efficient_Frontier"]
    assert len(fr) == 26  # 25 frontier points + benchmark row
    summ = w.sheets["9_Portfolio_Summary"]
    assert list(summ["Portfolio"])[0] == "Max-Sharpe (growth fund)"
    assert list(summ["Portfolio"])[2].startswith("Target 20%")
    assert list(summ["Portfolio"])[-1] == "Nifty 50 (benchmark)"
    assert summ["Holdings"].iloc[-1] == 50
    assert fr["Target_Return%"].iloc[-1] == summ["Ann_Return%"].iloc[-1]
    assert "PORTFOLIO SUMMARY (vs Nifty 50:" in out
    assert "Target-yield 'fund': requested 20%" in out
    assert "max weight cap 50%" in out and "shrinkage λ=0.5" in out
    assert "Top max-Sharpe weights (capped):" in out
    assert "beta=" in out


def test_build_without_target_and_capped_holdings(pb, monkeypatch, tmp_path, capsys):
    xl = _setup_build(pb, monkeypatch, tmp_path)
    pb.build("IN", 3, 0.065, max_weight=0.5)
    out = capsys.readouterr().out
    assert "Optimisation universe: 3 holdings (cap 3)" in out
    w = xl.writers[0]
    assert "8_Target_Return_Weights" not in w.sheets
    assert len(w.sheets["9_Portfolio_Summary"]) == 3  # max-sharpe, min-var, benchmark
    assert "Target-yield" not in out


def test_build_infeasible_target_message(pb, monkeypatch, tmp_path, capsys):
    _setup_build(pb, monkeypatch, tmp_path)
    pb.build("IN", 25, 0.065, max_weight=0.5, target_return=50.0)
    assert "not feasible (capped)" in capsys.readouterr().out


def test_build_insufficient_data(pb, monkeypatch, tmp_path, capsys):
    xl = _setup_build(pb, monkeypatch, tmp_path, with_index=False)
    pb.build("IN", 25, 0.065)
    assert "Insufficient data for optimisation." in capsys.readouterr().out
    assert xl.writers == []
    # index present but only one usable stock
    prices(99, 600).to_parquet(pb.INDEX_DIR / "NSEI.parquet")
    FakeWorkbook(monkeypatch, {"Triple_Hits": pd.DataFrame({"Symbol": ["S0"]})})
    pb.build("IN", 25, 0.065)
    assert "Insufficient data for optimisation." in capsys.readouterr().out
    assert xl.writers == []


def test_build_when_optimiser_returns_nothing(pb, monkeypatch, tmp_path, capsys):
    xl = _setup_build(pb, monkeypatch, tmp_path)
    monkeypatch.setattr(pb, "optimise", lambda *a, **k: {})
    pb.build("IN", 25, 0.065)
    out = capsys.readouterr().out
    assert list(xl.writers[0].sheets) == [
        "1_Triple_Hits", "2_Coffee_Can", "3_Other_Picks", "4_Risk_Metrics", "DISCLAIMER"
    ]  # fmt: skip
    assert "PORTFOLIO SUMMARY" not in out and "Insufficient" not in out


def test_build_forwards_optimiser_arguments(pb, monkeypatch, tmp_path):
    _setup_build(pb, monkeypatch, tmp_path)
    seen = {}

    def spy(rets, rf, **kw):
        seen.update(kw, rf=rf, cols=list(rets.columns))
        return {}

    monkeypatch.setattr(pb, "optimise", spy)
    pb.build("IN", 25, 0.07, max_weight=0.33, target_return=0.12, shrink=0.8)
    assert seen["max_weight"] == 0.33 and seen["target_return"] == 0.12
    assert seen["shrink"] == 0.8 and seen["rf"] == 0.07
    assert sorted(seen["cols"]) == ["S0", "S1", "S2", "S3", "S4"]


def test_optimise_keeps_column_with_exactly_90pct_data(pb):
    R = rets_frame(n=100, k=4)
    R.iloc[:10, 0] = np.nan  # exactly 90% non-null -> kept (>=), so 4 assets
    pb.optimise(R, 0.05)
    assert len(pb.fm.of("sharpe")[0].bounds) == 4
