"""liquidity.py (index build, tiers, annotate) + leverage_features.py."""

import numpy as np
import pandas as pd
import pytest

import leverage_features as lf
import liquidity as liq
from tests.w4_helpers import fake_module


# ---------------- liquidity ----------------
def test_tier_boundaries_default_and_market():
    assert liq.tier(None) == "Unknown"
    assert liq.tier(liq.HIGH) == "High" and liq.tier(liq.HIGH - 1) == "Medium"
    assert liq.tier(liq.MED) == "Medium" and liq.tier(liq.MED - 1) == "Low"
    assert liq.tier(20_000_000, "US") == "High" and liq.tier(19_999_999, "US") == "Medium"
    assert liq.tier(2_000_000, "US") == "Medium" and liq.tier(1_999_999, "US") == "Low"
    assert liq.tier(10_000_000, "ZZ") == "High"  # unknown market -> global defaults
    assert liq.tier(0) == "Low"


@pytest.fixture
def idx_path(monkeypatch, tmp_path):
    p = tmp_path / "liq.parquet"
    monkeypatch.setattr(liq, "INDEX_PATH", p)
    monkeypatch.setattr(liq, "_CACHE_MAP", {})
    return p


def _write_index(p):
    pd.DataFrame(
        {
            "Symbol": ["AAA", "BBB", "CCC", "DDD"],
            "Market": ["US", "US", "IN", "IN"],
            "turnover_usd": [50e6, 1.5e6, 6e6, 100.0],
            "ltp": [1.0, 2.0, 3.0, 4.0],
        }
    ).to_parquet(p)


def test_load_index_missing_is_empty(idx_path):
    assert liq._load_index().empty
    assert liq.liquid_symbols() == []
    assert liq._turnover_map() == {}


def test_turnover_lookup(idx_path):
    _write_index(idx_path)
    assert liq.turnover("CCC") == 6e6
    assert liq.turnover("NOPE") is None


def test_liquid_symbols_filters_sorts_top(idx_path):
    _write_index(idx_path)
    assert liq.liquid_symbols() == ["AAA", "CCC", "BBB"]  # >=1e6, desc
    assert liq.liquid_symbols(min_usd=6e6) == ["AAA", "CCC"]  # boundary inclusive
    assert liq.liquid_symbols("IN") == ["CCC"]
    assert liq.liquid_symbols(top=2) == ["AAA", "CCC"]
    assert liq.liquid_symbols("IN", min_usd=0) == ["CCC", "DDD"]


def test_annotate_with_market_column(idx_path):
    _write_index(idx_path)
    df = pd.DataFrame({"Symbol": ["AAA", "BBB", "CCC", "ZZZ"], "Market": ["US", "US", "IN", "IN"]})
    out = liq.annotate(df)
    # an unindexed symbol has NaN turnover, which is below every bar -> "Low" (not "Unknown")
    assert list(out["Liquidity"]) == ["High", "Low", "High", "Low"]
    assert out["Turnover_USD"].iloc[0] == 50e6
    assert "Turnover_USD" not in df.columns  # input untouched


def test_annotate_without_market_uses_global_tiers(idx_path):
    _write_index(idx_path)
    out = liq.annotate(pd.DataFrame({"Sym": ["AAA", "BBB", "DDD"]}), symbol_col="Sym")
    assert list(out["Liquidity"]) == ["High", "Medium", "Low"]


def test_annotate_passthrough(idx_path):
    assert liq.annotate(None) is None
    e = pd.DataFrame()
    assert liq.annotate(e) is e
    nocol = pd.DataFrame({"x": [1]})
    assert liq.annotate(nocol) is nocol


def test_annotate_unknown_symbol_is_unknown(idx_path):
    _write_index(idx_path)
    out = liq.annotate(pd.DataFrame({"Symbol": ["QQQ"]}))
    assert pd.isna(out["Turnover_USD"].iloc[0])
    assert out["Liquidity"].iloc[0] == "Low"


class _FakeYF:
    def __init__(self, table=None, boom=False):
        self.table = table
        self.boom = boom
        self.calls = []

    def download(self, tickers, period, progress):
        self.calls.append((tickers, period, progress))
        if self.boom:
            raise RuntimeError("down")
        return {"Close": self.table}


def test_fx_rates_live_missing_and_usd_excluded(monkeypatch):
    table = pd.DataFrame({"INR=X": [80.0, np.nan, 83.0], "EUR=X": [np.nan] * 3})
    yf = _FakeYF(table)
    fake_module(monkeypatch, "yfinance", download=yf.download)
    fx = liq._fx_rates(["USD", "INR", "EUR", "INR", "JPY"])
    assert fx["USD"] == 1.0 and fx["INR"] == 83.0
    assert fx["EUR"] is None  # all-NaN column
    assert fx["JPY"] is None  # column absent
    assert yf.calls[0][0] == ["EUR=X", "INR=X", "JPY=X"]  # sorted, deduped, no USD
    assert yf.calls[0][1:] == ("5d", False)


def test_fx_rates_download_failure(monkeypatch):
    fake_module(monkeypatch, "yfinance", download=_FakeYF(boom=True).download)
    assert liq._fx_rates(["USD", "INR"]) == {"USD": 1.0, "INR": None}


def _ohlc(close, vol, n=25):
    return pd.DataFrame({"Close": [close] * n, "Volume": [vol] * n})


def test_build_index(monkeypatch, idx_path, capsys):
    data = {
        "US": {
            "AAA": _ohlc(10.0, 1000),
            "SHORT": _ohlc(10.0, 1000, n=19),  # < 20 bars -> skipped
            "NOVOL": pd.DataFrame({"Close": [1.0] * 25}),
            "ZERO": _ohlc(10.0, 0),  # zero turnover -> skipped
            "NONE": None,
        },
        "UK": {"UKX": _ohlc(100.0, 200)},
        "IN": {"INX": _ohlc(50.0, 100)},  # no FX rate -> whole market skipped
    }
    fake_module(monkeypatch, "screener_kit", MARKETS=["US", "UK", "IN"], load=lambda m: data[m])
    monkeypatch.setattr(liq, "_fx_rates", lambda ccys: {"USD": 1.0, "GBP": 0.5, "INR": None})
    idx = liq.build_index()
    rows = idx.set_index("Symbol")
    assert set(idx["Symbol"]) == {"AAA", "UKX"}
    assert rows.loc["AAA", "turnover_usd"] == pytest.approx(10_000.0)
    # UK: quoted in pence -> /100, then / rate(GBP=0.5)
    assert rows.loc["UKX", "turnover_usd"] == pytest.approx(100.0 * 200 / 100.0 / 0.5)
    assert rows.loc["AAA", "ltp"] == 10.0
    assert idx_path.exists()
    out = capsys.readouterr().out
    assert "US: indexed 1 stocks" in out and "liquidity index: 2 symbols" in out
    assert "IN: indexed" not in out


def test_build_index_empty_writes_nothing(monkeypatch, idx_path, capsys):
    fake_module(monkeypatch, "screener_kit", MARKETS=["US"], load=lambda m: {})
    monkeypatch.setattr(liq, "_fx_rates", lambda c: {"USD": 1.0})
    assert liq.build_index(verbose=False).empty
    assert not idx_path.exists()
    assert capsys.readouterr().out == ""


# ---------------- leverage_features ----------------
@pytest.mark.parametrize(
    "hist,expected",
    [
        ([100, 150], 0.5),
        ([200, 100, 50], -0.75),
        ((10.0, 12.0), 0.2),
        (np.array([4.0, 5.0]), 0.25),
        ([-10, -5], 0.5),  # abs(first) in the denominator
    ],
)
def test_debt_trend_values(hist, expected):
    assert lf._debt_trend(hist) == pytest.approx(expected)


@pytest.mark.parametrize(
    "hist", [[1], [], [0, 5], "abc", None, ["x", "y"], [np.nan, 1.0], [np.inf, 1.0], 7]
)
def test_debt_trend_nan_cases(hist):
    assert np.isnan(lf._debt_trend(hist))


def test_debt_trend_drops_nonfinite_before_slope():
    assert lf._debt_trend([100, np.nan, 150]) == pytest.approx(0.5)


@pytest.fixture
def cache(monkeypatch, tmp_path):
    monkeypatch.setattr(lf, "CACHE", tmp_path)
    return tmp_path


def test_leverage_frame_missing_corrupt_empty_nosymbol(cache):
    cols = ["Symbol", *lf.LEV_FEATURES]
    assert list(lf.leverage_frame("IN").columns) == cols
    (cache / "IN.parquet").write_text("junk")
    assert list(lf.leverage_frame("IN").columns) == cols and lf.leverage_frame("IN").empty
    pd.DataFrame({"Symbol": []}).to_parquet(cache / "EU.parquet")
    assert lf.leverage_frame("EU").empty
    pd.DataFrame({"foo": [1]}).to_parquet(cache / "JP.parquet")
    assert lf.leverage_frame("JP").empty


def test_leverage_frame_features_and_dedup(cache):
    pd.DataFrame(
        {
            "Symbol": ["A", "B", "A"],
            "debt_to_equity": ["1.5", "x", "2.5"],
            "debt_to_assets": [0.4, 0.3, 0.6],
            "debt_to_assets_prev": [0.3, 0.5, 0.5],
            "debt_history": [[1.0, 2.0], [5.0], [10.0, 5.0]],
        }
    ).to_parquet(cache / "IN.parquet")
    out = lf.leverage_frame("IN").set_index("Symbol")
    assert list(out.index) == ["B", "A"]  # first-seen order of the surviving (last) rows
    assert out.loc["A", "lev_de"] == 2.5  # keep="last"
    assert out.loc["A", "lev_da_delta"] == pytest.approx(0.1)
    assert out.loc["A", "lev_debt_trend"] == pytest.approx(-0.5)
    assert np.isnan(out.loc["B", "lev_de"])  # coerced
    assert out.loc["B", "lev_da_delta"] == pytest.approx(-0.2)
    assert np.isnan(out.loc["B", "lev_debt_trend"])


def test_leverage_frame_without_optional_columns(cache):
    pd.DataFrame({"Symbol": ["A"]}).to_parquet(cache / "IN.parquet")
    out = lf.leverage_frame("IN")
    assert out["Symbol"].tolist() == ["A"]
    assert out[lf.LEV_FEATURES].isna().all().all()


def test_merge_leverage_left_join_and_fallback(cache):
    pd.DataFrame({"Symbol": ["A"], "debt_to_equity": [3.0]}).to_parquet(cache / "IN.parquet")
    f = pd.DataFrame({"Symbol": ["A", "Z"], "x": [1, 2]})
    out = lf.merge_leverage(f, "IN")
    assert out["lev_de"].iloc[0] == 3.0 and np.isnan(out["lev_de"].iloc[1])
    assert len(out) == 2
    nof = pd.DataFrame({"Symbol": ["A"]})
    out2 = lf.merge_leverage(nof, "XX")  # no cache -> NaN columns added
    assert all(c in out2 and out2[c].isna().all() for c in lf.LEV_FEATURES)


def test_merge_leverage_keeps_existing_column_and_no_symbol(cache):
    f = pd.DataFrame({"Symbol": ["A"], "lev_de": [9.0]})
    out = lf.merge_leverage(f, "XX")
    assert out["lev_de"].iloc[0] == 9.0  # not overwritten
    nosym = pd.DataFrame({"a": [1]})
    pd.DataFrame({"Symbol": ["A"], "debt_to_equity": [1.0]}).to_parquet(cache / "IN.parquet")
    out2 = lf.merge_leverage(nosym, "IN")
    assert list(out2.columns) == ["a", *lf.LEV_FEATURES]


def test_leverage_main(cache, monkeypatch, capsys):
    pd.DataFrame({"Symbol": ["A"], "debt_to_equity": [1.0]}).to_parquet(cache / "IN.parquet")
    monkeypatch.setattr("sys.argv", ["lf", "--market", "IN"])
    assert lf.main() == 0
    out = capsys.readouterr().out
    assert "IN: 1 symbols with leverage features" in out and "lev_de" in out
    monkeypatch.setattr("sys.argv", ["lf", "--market", "QQ"])
    lf.main()
    assert capsys.readouterr().out.strip() == "QQ: 0 symbols with leverage features"
