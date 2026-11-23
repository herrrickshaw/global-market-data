"""Helpers for importing repo modules fresh, with fakes, without leaking sys.modules."""

import importlib
import sys
import types


def fake_module(monkeypatch, name, **attrs):
    """Install a throwaway module object under `name` (restored at teardown)."""
    m = types.ModuleType(name)
    for k, v in attrs.items():
        setattr(m, k, v)
    monkeypatch.setitem(sys.modules, name, m)
    return m


def fresh_import(monkeypatch, name):
    """Import `name` anew; the prior sys.modules entry (or absence) is restored afterwards."""
    monkeypatch.delitem(sys.modules, name, raising=False)
    mod = importlib.import_module(name)
    monkeypatch.setitem(sys.modules, name, mod)
    return mod
