"""market_memory: two-tier (LTM/STM) upsert, eviction, update/create flows (fully offline)."""

import datetime as dt

import numpy as np
import pandas as pd
import pytest

import frames
import market_memory as mm
from tests.w4_helpers import fake_module

TODAY = dt.date.today()


@pytest.fixture
def mem(monkeypatch, tmp_path):
    seed = tmp_path / "seed"
    ltm = seed / "ltm"
    ltm.mkdir(parents=True)
    monkeypatch.setattr(mm, "SEED_DIR", seed)
    monkeypatch.setattr(mm, "LTM_DIR", ltm)
    return mm


def _long(sym="A", days_ago_start=10, n=5, close0=10.0, step=1.0):
    dates = [pd.Timestamp(TODAY - dt.timedelta(days=days_ago_start - i)) for i in range(n)]
    c = close0 + step * np.arange(n)
    return pd.DataFrame(
        {"Date": dates, "Open": c, "High": c, "Low": c, "Close": c, "Volume": 100, "Symbol": sym}
    )


def test_paths(mem):
    assert mem._ltm_path("US") == mem.LTM_DIR / "US.parquet"
    assert mem._stm_path("IN") == mem.SEED_DIR / "cleaned_long.parquet"
    assert mem._stm_path("US") == mem.SEED_DIR / "cleaned_long_US.parquet"


def test_read_long_missing_and_parses_dates(mem):
    e = mem._read_long(mem.SEED_DIR / "nope.parquet")
    assert e.empty and list(e.columns) == ["Date", *frames.OHLCV, "Symbol"]
    p = mem.SEED_DIR / "x.parquet"
    d = _long()
    d["Date"] = d["Date"].dt.strftime("%Y-%m-%d")
    d.to_parquet(p)
    assert str(mem._read_long(p)["Date"].dtype).startswith("datetime64")


def test_upsert_newest_wins_sorted_and_passthrough(mem):
    old = _long("A", 10, 3, close0=1.0)
    new = _long("A", 10, 3, close0=100.0).iloc[1:]  # overlaps last two dates
    other = _long("B", 10, 1)
    out = mem._upsert(old, pd.concat([new, other], ignore_index=True))
    a = out[out.Symbol == "A"]
    assert list(a["Close"]) == [1.0, 101.0, 102.0]  # first kept, overlaps replaced by new
    assert list(out["Symbol"]) == ["A", "A", "A", "B"]
    assert mem._upsert(old, None) is old
    empty = pd.DataFrame()
    assert mem._upsert(old, empty) is old
    only_new = mem._upsert(pd.DataFrame(), new)
    assert len(only_new) == 2
    assert len(mem._upsert(None, new)) == 2


def test_evict_cutoff_boundary(mem):
    years = 1
    cutoff_days = int(years * 365.25) + 5
    rows = _long("A", 0, 1)
    keep = rows.copy()
    keep["Date"] = pd.Timestamp(TODAY - dt.timedelta(days=cutoff_days))
    drop = rows.copy()
    drop["Date"] = pd.Timestamp(TODAY - dt.timedelta(days=cutoff_days + 1))
    out = mem._evict(pd.concat([keep, drop], ignore_index=True), years)
    assert len(out) == 1 and out["Date"].iloc[0] == keep["Date"].iloc[0]
    empty = pd.DataFrame()
    assert mem._evict(empty, 1) is empty


def test_commit_creates_both_tiers_and_reports(mem, capsys):
    new = pd.concat([_long("A"), _long("B", n=3)], ignore_index=True)
    r = mem.commit("US", new)
    assert r == {
        "market": "US",
        "ltm_symbols": 2,
        "stm_symbols": 2,
        "latest": str(TODAY - dt.timedelta(days=6)),
    }
    assert mem._ltm_path("US").exists() and mem._stm_path("US").exists()
    out = capsys.readouterr().out
    assert "US: LTM 2 syms / STM 2 syms" in out and "ingest-gate[US]" in out
    assert set(mem.read("US", "ltm")) == {"A", "B"} and set(mem.read("US")) == {"A", "B"}
    assert mem.read("US", "ltm", min_bars=4).keys() == {"A"}


def test_commit_old_rows_stay_in_ltm_not_stm(mem):
    old = _long("A", days_ago_start=800, n=3)  # ~2.2 years old: inside 10y LTM, outside 1y STM
    recent = _long("A", days_ago_start=5, n=2)
    mem.commit("US", pd.concat([old, recent], ignore_index=True), verbose=False)
    assert len(mem.read("US", "ltm")["A"]) == 5
    assert len(mem.read("US", "stm")["A"]) == 2


def test_commit_evicts_beyond_ltm_window(mem):
    ancient = _long("Z", days_ago_start=int(mem.LTM_YEARS * 365.25) + 400, n=2)
    mem.commit("US", pd.concat([ancient, _long("A")], ignore_index=True), verbose=False)
    assert set(mem.read("US", "ltm")) == {"A"}


def test_commit_upserts_into_existing(mem):
    mem.commit("US", _long("A", 10, 3, close0=1.0), verbose=False)
    mem.commit("US", _long("A", 10, 4, close0=50.0), verbose=False)
    a = mem.read("US", "ltm")["A"]
    assert len(a) == 4 and a["Close"].iloc[0] == pytest.approx(50.0)


def test_commit_survives_gate_failure(mem, monkeypatch, capsys):
    import ingest_gate

    def boom(*a, **k):
        raise RuntimeError("gate down " * 10)

    monkeypatch.setattr(ingest_gate, "gate_frame", boom)
    r = mem.commit("US", _long("A"))
    assert r["ltm_symbols"] == 1
    assert "ingest-gate skipped (" + ("gate down " * 10)[:50] + ")" in capsys.readouterr().out


def test_commit_survives_report_failure_and_quiet(mem, monkeypatch, capsys):
    import ingest_gate

    monkeypatch.setattr(ingest_gate, "report_after", lambda *a, **k: 1 / 0)
    r = mem.commit("US", _long("A"), verbose=False)
    assert r["ltm_symbols"] == 1 and capsys.readouterr().out == ""


def test_commit_empty_latest_placeholder(mem):
    r = mem.commit("US", pd.DataFrame(columns=["Date", *frames.OHLCV, "Symbol"]), verbose=False)
    assert r["latest"] == "—" and r["ltm_symbols"] == 0


def test_ensure_stm_seeds(mem, capsys):
    mem.commit("US", _long("A"), verbose=False)
    mem._stm_path("US").unlink()
    assert mem.ensure_stm_seeds(["US", "JP"]) == 1  # JP has no LTM
    assert mem._stm_path("US").exists() and not mem._stm_path("JP").exists()
    assert "derived 1 STM seed(s)" in capsys.readouterr().out
    assert mem.ensure_stm_seeds(["US"]) == 0  # already there
    assert capsys.readouterr().out == ""
    assert mem.ensure_stm_seeds(["US"], force=True, verbose=False) == 1


def test_drop_symbols_from_both_tiers(mem, capsys):
    mem.commit("US", pd.concat([_long("A"), _long("B")], ignore_index=True), verbose=False)
    r = mem.drop("US", ["A", "ZZ"])
    assert r == {"market": "US", "ltm_symbols": 1, "stm_symbols": 1}
    assert set(mem.read("US", "ltm")) == {"B"} and set(mem.read("US", "stm")) == {"B"}
    assert "dropped 2 syms" in capsys.readouterr().out
    mem.drop("US", ["B"], verbose=False)
    assert capsys.readouterr().out == ""


def test_seed_from_committed(mem):
    assert mem.seed_from_committed("US") == {"market": "US", "error": "no committed seed"}
    frames.write_seed(frames.long_to_dict(_long("A")), mem._stm_path("US"))
    r = mem.seed_from_committed("US", verbose=False)
    assert r["ltm_symbols"] == 1 and mem._ltm_path("US").exists()
    # IN uses the un-suffixed seed file
    frames.write_seed(frames.long_to_dict(_long("R")), mem.SEED_DIR / "cleaned_long.parquet")
    assert mem.seed_from_committed("IN", verbose=False)["ltm_symbols"] == 1


def test_fetch_new_in_prefers_live_cache_then_stm(mem, monkeypatch, tmp_path):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    monkeypatch.setenv("BHAV_CACHE", str(live_dir))
    frames.write_seed(frames.long_to_dict(_long("STM")), mem._stm_path("IN"))
    got = mem._fetch_new("IN", "5d", False)
    assert set(got["Symbol"]) == {"STM"}  # live file absent -> STM seed
    _long("LIVE").to_parquet(live_dir / "cleaned_long.parquet")
    assert set(mem._fetch_new("IN", "5d", False)["Symbol"]) == {"LIVE"}


def test_fetch_new_other_market_uses_tracked_symbols(mem, monkeypatch, capsys):
    mem.commit("US", _long("A"), verbose=False)
    calls = {}

    def fetch(syms, period, min_bars, verbose):
        calls.update(syms=syms, period=period, min_bars=min_bars, verbose=verbose)
        return {
            "A": pd.DataFrame(
                {"Open": [1.0], "High": [1.0], "Low": [1.0], "Close": [1.0], "Volume": [1]},
                index=pd.to_datetime([TODAY]),
            )
        }

    fake_module(monkeypatch, "data_sources", fetch=fetch)
    out = mem._fetch_new("US", "5d", True)
    assert calls == {"syms": ["A"], "period": "5d", "min_bars": 1, "verbose": False}
    assert list(out["Symbol"]) == ["A"]
    assert "US: fetching 1 symbols (period=5d)" in capsys.readouterr().out


def test_fetch_new_falls_back_to_universe_or_skips(mem, monkeypatch, capsys):
    seen = {}
    fake_module(
        monkeypatch, "data_sources", fetch=lambda syms, **k: seen.setdefault("s", syms) and {}
    )
    fake_module(monkeypatch, "universe_sources", get_universe=lambda m: ["X", "Y"])
    out = mem._fetch_new("JP", "5d", False)
    assert seen["s"] == ["X", "Y"] and out.empty

    def nope(m):
        raise RuntimeError("no universe available " * 5)

    fake_module(monkeypatch, "universe_sources", get_universe=nope)
    out = mem._fetch_new("JP", "5d", True)
    assert out.empty and list(out.columns) == ["Date", *frames.OHLCV, "Symbol"]
    assert (
        "JP: no universe (" + ("no universe available " * 5)[:40] + ") — skipped"
        in capsys.readouterr().out
    )
    mem._fetch_new("JP", "5d", False)
    assert capsys.readouterr().out == ""


def test_update_bootstraps_thin_ltm_from_seed(mem, monkeypatch):
    frames.write_seed(
        frames.long_to_dict(_long("OLD", 200, 150)), mem.SEED_DIR / "cleaned_long_US.parquet"
    )
    monkeypatch.setattr(mem, "_fetch_new", lambda m, period, verbose: _long("NEW", 3, 2))
    r = mem.update("US", verbose=False)
    assert r["ltm_symbols"] == 2
    assert set(mem.read("US", "ltm")) == {"OLD", "NEW"}


def test_update_no_bootstrap_when_ltm_has_history_or_market_in(mem, monkeypatch):
    mem.commit("US", _long("HIST", 100, 61, close0=1.0), verbose=False)  # span = 60 days
    # the "committed seed" is the same file the STM tier is derived into, so write it afterwards
    frames.write_seed(
        frames.long_to_dict(_long("SEEDED", 200, 5)), mem.SEED_DIR / "cleaned_long_US.parquet"
    )
    monkeypatch.setattr(mem, "_fetch_new", lambda m, period, verbose: _long("NEW", 3, 2))
    mem.update("US", verbose=False)
    assert "SEEDED" not in mem.read("US", "ltm")  # span 60 >= 60: no bootstrap
    # IN never bootstraps
    frames.write_seed(
        frames.long_to_dict(_long("INSEED", 200, 5)), mem.SEED_DIR / "cleaned_long_IN.parquet"
    )
    mem.update("IN", verbose=False)
    assert "INSEED" not in mem.read("IN", "ltm")


def test_update_bootstraps_when_span_just_under_60(mem, monkeypatch):
    mem.commit("US", _long("HIST", 100, 60, close0=1.0), verbose=False)  # span = 59 days
    frames.write_seed(
        frames.long_to_dict(_long("SEEDED", 200, 3)), mem.SEED_DIR / "cleaned_long_US.parquet"
    )
    monkeypatch.setattr(mem, "_fetch_new", lambda m, period, verbose: _long("NEW", 3, 2))
    mem.update("US", verbose=False)
    assert "SEEDED" in mem.read("US", "ltm")


def test_init_uses_ltm_years_period(mem, monkeypatch):
    seen = {}
    monkeypatch.setattr(
        mem, "update", lambda m, period, verbose: seen.update(m=m, p=period, v=verbose) or {"ok": 1}
    )
    assert mem.init("US", verbose=False) == {"ok": 1}
    assert seen == {"m": "US", "p": "10y", "v": False}


def test_update_all_skips_closed_isolates_failures(mem, monkeypatch, capsys):
    import market_holidays

    monkeypatch.setattr(market_holidays, "is_market_open_today", lambda m: m != "JP")
    calls = []

    def upd(m, period, verbose):
        calls.append((m, period))
        if m == "KR":
            raise RuntimeError("boom " * 20)
        return {"market": m}

    monkeypatch.setattr(mem, "update", upd)
    out = mem.update_all(["US", "JP", "KR", "IN"], period="3d")
    assert calls == [("US", "3d"), ("KR", "3d"), ("IN", "3d")]
    assert out[0] == {"market": "US"} and out[1]["market"] == "KR" and "boom" in out[1]["error"]
    assert out[2] == {"market": "IN"}
    txt = capsys.readouterr().out
    assert "daily update (period=3d) for 4 markets" in txt
    assert "skipping closed markets: ['JP']" in txt
    assert "KR: FAILED (" + ("boom " * 20)[:60] + ")" in txt


def test_update_all_create_ignores_holidays(mem, monkeypatch, capsys):
    import market_holidays

    monkeypatch.setattr(market_holidays, "is_market_open_today", lambda m: False)
    monkeypatch.setattr(mem, "init", lambda m, verbose: {"init": m})
    out = mem.update_all(["US", "JP"], create=True)
    assert out == [{"init": "US"}, {"init": "JP"}]
    assert "CREATE 5y backfill" in capsys.readouterr().out


def test_update_all_default_markets_and_quiet(mem, monkeypatch, capsys):
    import market_holidays

    monkeypatch.setattr(market_holidays, "is_market_open_today", lambda m: True)
    monkeypatch.setattr(mem, "update", lambda m, period, verbose: {"market": m})
    out = mem.update_all(verbose=False)
    assert [o["market"] for o in out] == mem.MARKETS and capsys.readouterr().out == ""


def test_status_report(mem):
    mem.commit("US", pd.concat([_long("A"), _long("B", n=3)], ignore_index=True), verbose=False)
    st = mem.status(["US", "JP"]).set_index(["market", "tier"])
    assert st.loc[("US", "ltm"), "symbols"] == 2 and st.loc[("US", "ltm"), "rows"] == 8
    assert st.loc[("US", "stm"), "start"] == str(TODAY - dt.timedelta(days=10))
    assert st.loc[("JP", "ltm"), "start"] == "—" and st.loc[("JP", "stm"), "rows"] == 0
    assert len(mem.status()) == 2 * len(mem.MARKETS)


def test_main_status_seeds_and_daily(mem, monkeypatch, capsys):
    mem.commit("US", _long("A"), verbose=False)
    monkeypatch.setattr("sys.argv", ["mm", "--status", "--market", "US"])
    assert mem.main() == 0
    out = capsys.readouterr().out
    assert "US" in out and "ltm" in out and "JP" not in out

    seeded = []
    monkeypatch.setattr(mem, "seed_from_committed", lambda m, verbose: seeded.append((m, verbose)))
    monkeypatch.setattr("sys.argv", ["mm", "--from-seeds", "--market", "US", "JP", "--quiet"])
    assert mem.main() == 0
    assert seeded == [("US", False), ("JP", False)]

    got = {}
    monkeypatch.setattr(mem, "update_all", lambda **k: got.update(k) or [])
    monkeypatch.setattr("sys.argv", ["mm", "--create", "--period", "9d"])
    assert mem.main() == 0
    assert got == {"markets": None, "period": "9d", "create": True, "verbose": True}


def test_main_reports_failure(mem, monkeypatch, capsys):
    def boom(**k):
        raise RuntimeError("kaput")

    monkeypatch.setattr(mem, "update_all", boom)
    monkeypatch.setattr("sys.argv", ["mm"])
    assert mem.main() == 1
    assert "market memory update FAILED: kaput" in capsys.readouterr().out
