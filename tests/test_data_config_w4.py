"""data_config: split ranges, source registry, validation rules, freshness and consistency checks."""

from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest

import data_config as dc


def test_date_splits_ordering_and_overlap_guard(monkeypatch):
    assert dc.DateSplits.get_train_range() == ("2015-01-01", "2021-12-31")
    assert dc.DateSplits.get_validation_range() == ("2022-01-01", "2022-12-31")
    assert dc.DateSplits.get_test_range() == ("2023-01-01", "2024-06-30")
    assert dc.DateSplits.validate_no_overlap() is True
    monkeypatch.setattr(dc.DateSplits, "VAL_START", "2021-12-31")  # equal to TRAIN_END: overlap
    with pytest.raises(AssertionError, match="Train-Val overlap"):
        dc.DateSplits.validate_no_overlap()
    monkeypatch.undo()
    monkeypatch.setattr(dc.DateSplits, "TEST_START", "2022-12-31")
    with pytest.raises(AssertionError, match="Val-Test overlap"):
        dc.DateSplits.validate_no_overlap()


def test_source_registry():
    assert dc.DataSourceRegistry.get_source("IN") is dc.DataSourceRegistry.INDIA_PRIMARY
    assert dc.DataSourceRegistry.get_source("US")["name"] == "cleaned_long_US.parquet"
    assert dc.DataSourceRegistry.get_source("ZZ") is None
    assert dc.DataSourceRegistry.get_all_markets() == ["IN", "US", "CN", "JP", "KR", "AU", "EU"]
    for m, src in dc.DataSourceRegistry.GLOBAL_SOURCES.items():
        assert src["path"] == f"cache_seed/cleaned_long_{m}.parquet"


def _ohlc(n=100, **over):
    base = {
        "open": 10.0, "high": 12.0, "low": 9.0, "close": np.linspace(10, 11, n), "volume": 100.0,
        "date": pd.date_range("2024-01-01", periods=n),
    }
    base.update(over)
    return pd.DataFrame(base)


def test_validate_clean_frame_has_no_errors():
    assert dc.DataValidationRules.validate_dataframe(_ohlc()) == []


def test_validate_missing_columns_collected_or_raised():
    df = _ohlc().drop(columns=["volume", "date"])
    errors = dc.DataValidationRules.validate_dataframe(df)
    assert errors == ["Missing critical column: volume", "Missing critical column: date"]
    with pytest.raises(ValueError, match="Missing critical column: volume"):
        dc.DataValidationRules.validate_dataframe(df, raise_on_error=True)


def test_validate_null_threshold_boundary():
    n = 100
    five = _ohlc(n)
    five.loc[:4, "volume"] = np.nan  # exactly 5% nulls: allowed (strictly greater than max fails)
    assert dc.DataValidationRules.validate_dataframe(five) == []
    six = _ohlc(n)
    six.loc[:5, "volume"] = np.nan
    errors = dc.DataValidationRules.validate_dataframe(six)
    assert errors == ["Column volume has 6.0% nulls (max: 5.0%)"]


def test_validate_high_below_low_count():
    df = _ohlc(10)
    df.loc[[2, 3], "high"] = 5.0  # below the 9.0 low
    assert dc.DataValidationRules.validate_dataframe(df) == ["2 rows with high < low"]
    df2 = _ohlc(10, high=9.0)  # high == low is fine
    assert dc.DataValidationRules.validate_dataframe(df2) == []


def test_validate_adds_returns_column_to_input():
    df = _ohlc(5)
    dc.DataValidationRules.validate_dataframe(df)
    assert "returns" in df.columns  # documents the in-place side effect on the caller's frame


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason="data_config.validate_dataframe compares pct_change() (a fraction: 1.0 == +100%) with "
    "MAX_DAILY_MOVE_PCT = 50, so only moves above +5000% are ever flagged",
)
def test_validate_extreme_move_rule_uses_percent_semantics():
    # a doubling every other bar is a +100% move, far beyond the 50% anomaly threshold
    close = np.where(np.arange(100) % 2 == 0, 10.0, 20.0)
    errors = dc.DataValidationRules.validate_dataframe(_ohlc(100, close=close))
    assert any("extreme daily moves" in e for e in errors)


def test_universe_presets_and_default():
    assert dc.Universe.get_universe("india_focus")["market"] == "IN"
    assert dc.Universe.get_universe("us_large_cap")["count_limit"] == 500
    assert dc.Universe.get_universe("global_all")["exhaustive"] is True
    assert dc.Universe.get_universe("nonsense") is dc.Universe.INDIA_FOCUS
    assert dc.Universe.get_universe() is dc.Universe.INDIA_FOCUS
    assert len(dc.Universe.INDIA_FOCUS["symbols"]) == 10


NOW = datetime.now()


@pytest.mark.parametrize(
    "req,age,fresh",
    [
        ("INTRADAY_SCREENER", timedelta(minutes=14), True),
        ("INTRADAY_SCREENER", timedelta(minutes=16), False),
        ("DAILY_SCREENER", timedelta(hours=23), True),
        ("DAILY_SCREENER", timedelta(hours=25), False),
        ("WEEKLY_ANALYSIS", timedelta(days=7, hours=2), True),  # .days truncates -> 7 days is still fresh
        ("WEEKLY_ANALYSIS", timedelta(days=8), False),
        ("BACKTEST_HISTORICAL", timedelta(days=364), True),
        ("BACKTEST_HISTORICAL", timedelta(days=366), False),
    ],
)
def test_check_freshness_boundaries(req, age, fresh):
    ok, msg = dc.FreshnessRequirements.check_freshness(datetime.now() - age, getattr(dc.FreshnessRequirements, req))
    assert ok is fresh and msg.startswith("Age: ")


def test_check_freshness_messages_and_unknown_requirement():
    ok, msg = dc.FreshnessRequirements.check_freshness(datetime.now() - timedelta(minutes=5), dc.FreshnessRequirements.INTRADAY_SCREENER)
    assert "min (max: 15 min)" in msg
    ok, msg = dc.FreshnessRequirements.check_freshness(datetime.now() - timedelta(hours=2), dc.FreshnessRequirements.DAILY_SCREENER)
    assert "hours (max: 24 hours)" in msg
    ok, msg = dc.FreshnessRequirements.check_freshness(datetime.now(), {"purpose": "x"})
    assert ok is True and msg == "Unknown freshness requirement"


@pytest.mark.parametrize(
    "a,b,tol,expected",
    [(100.0, 98.0, 2.0, True), (100.0, 97.9, 2.0, False), (0.0, 0.005, 5.0, True), (0.0, 0.02, 5.0, False),
     (-100.0, -98.0, 2.0, True), (100.0, 100.0, 0.0, True), (200.0, 100.0, 50.0, True), (200.0, 99.0, 50.0, False)],
)
def test_acceptable_difference(a, b, tol, expected):
    assert dc.ConsistencyRules.acceptable_difference(a, b, tol) is expected


def test_data_config_split_for_purpose():
    cfg = dc.DataConfig()
    assert cfg.get_split_for_purpose("backtest") == dc.DateSplits.get_test_range()
    assert cfg.get_split_for_purpose("validation") == dc.DateSplits.get_validation_range()
    assert cfg.get_split_for_purpose("training") == dc.DateSplits.get_train_range()
    assert cfg.get_split_for_purpose("anything else") == dc.DateSplits.get_test_range()
    assert cfg.consistency.PRICE_DISCREPANCY_PCT == 2.0 and cfg.validation.MIN_RECORDS_PER_SYMBOL == 252


def test_filter_data_by_split_inclusive_bounds():
    dates = ["2022-12-31", "2023-01-01", "2024-06-30", "2024-07-01", "2021-12-31", "2022-01-01"]
    df = pd.DataFrame({"d": dates, "v": range(6)})
    assert list(dc.filter_data_by_split(df.copy(), "d")["v"]) == [1, 2]  # default = test split, both ends inclusive
    assert list(dc.filter_data_by_split(df.copy(), "d", "validation")["v"]) == [0, 5]
    assert list(dc.filter_data_by_split(df.copy(), "d", "train")["v"]) == [4]
    with pytest.raises(ValueError, match="Unknown split: holdout"):
        dc.filter_data_by_split(df.copy(), "d", "holdout")
    mutated = df.copy()
    dc.filter_data_by_split(mutated, "d")
    assert mutated["d"].dtype.kind == "M"  # the date column is converted in place


def test_validate_universe_consistency():
    df = pd.DataFrame({"symbol": ["A", "B", "C", "D"]})
    ok = {"symbols": ["A", "B", "C", "D", "E"]}
    assert dc.validate_universe_consistency(ok, df) == []  # 4/5 = 80% is acceptable
    bad = {"symbols": ["A", "B", "C", "X", "Y"]}
    assert dc.validate_universe_consistency(bad, df) == ["Only 3/5 defined symbols found in data"]
    assert dc.validate_universe_consistency({"symbols": None}, df) == []
    assert dc.validate_universe_consistency({"min_market_cap_usd": 1}, df) == []
    assert dc.validate_universe_consistency({"symbols": ["A", "B"]}, pd.DataFrame({"symbol": []})) == ["Only 0/2 defined symbols found in data"]
