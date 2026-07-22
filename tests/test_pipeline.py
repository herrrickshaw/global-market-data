# Tests for the CRUD data-pipeline manager. Pure logic, no network.
import json


def _fresh(tmp_path, monkeypatch):
    import pipeline as p
    monkeypatch.setattr(p, "STATE", tmp_path / "state.json")
    return p


def test_promote_demote_crud(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    p.promote("IN", {"AAA": ["darvas"], "BBB": ["golden"]}, verbose=False)
    assert set(p.promoted("IN")) == {"AAA", "BBB"}
    assert p.promoted("IN")["AAA"]["filters"] == ["darvas"]
    # demote removes only the named symbol
    p.demote("IN", ["AAA"], verbose=False)
    assert set(p.promoted("IN")) == {"BBB"}


def test_promote_preserves_since_date(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    p.promote("IN", {"AAA": ["darvas"]}, verbose=False)
    since = p.promoted("IN")["AAA"]["since"]
    # re-promote with new filters keeps original since-date
    p.promote("IN", {"AAA": ["darvas", "golden"]}, verbose=False)
    assert p.promoted("IN")["AAA"]["since"] == since
    assert p.promoted("IN")["AAA"]["filters"] == ["darvas", "golden"]


def test_sync_conviction_gate_and_diff(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    # mock the filters: X clears 2, Y clears 1, Z clears 3
    monkeypatch.setattr(p, "filter_clearers",
                        lambda m, min_turnover_usd=0: {"X": ["a", "b"], "Y": ["a"], "Z": ["a", "b", "c"]})
    r = p.sync("IN", min_filters=2, verbose=False)
    assert r["watchlist"] == 2                     # only X and Z clear >=2
    assert set(p.promoted("IN")) == {"X", "Z"}
    assert r["promoted"] == 2 and r["demoted"] == 0

    # next sync: Z drops out, W appears with 2 filters → promote W, demote Z
    monkeypatch.setattr(p, "filter_clearers",
                        lambda m, min_turnover_usd=0: {"X": ["a", "b"], "W": ["a", "b"]})
    r2 = p.sync("IN", min_filters=2, verbose=False)
    assert set(p.promoted("IN")) == {"X", "W"}
    assert r2["promoted"] == 1 and r2["demoted"] == 1


def test_status_reports_watchlist(tmp_path, monkeypatch):
    p = _fresh(tmp_path, monkeypatch)
    p.promote("US", {"AAPL": ["piotroski", "roce"]}, verbose=False)
    df = p.status(["US"])
    assert not df.empty
    assert int(df.iloc[0]["watchlist"]) == 1
