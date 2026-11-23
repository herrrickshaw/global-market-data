"""screener_in_auth: login flow, export download, page ratios, fundamentals parsing (all faked)."""

import pandas as pd
import pytest

from tests.w4_helpers import fresh_import

DUMMY_EMAIL = "dummy-user@example.invalid"
DUMMY_PW = "dummy-not-a-real-secret"  # noqa: S105


@pytest.fixture
def sa(monkeypatch, tmp_path):
    mod = fresh_import(monkeypatch, "screener_in_auth")
    # keep the .env lookup inside tmp_path
    monkeypatch.setattr(mod, "__file__", str(tmp_path / "screener_in_auth.py"))
    real_session = mod.session
    real_session.cache_clear()
    monkeypatch.delenv("SCREENER_EMAIL", raising=False)
    monkeypatch.delenv("SCREENER_PASSWORD", raising=False)
    yield mod
    real_session.cache_clear()


class Resp:
    def __init__(self, text="", status=200, content=b""):
        self.text = text
        self.status_code = status
        self.content = content


class FakeSession:
    def __init__(self, pages=None, post_resp=None, post_exc=False, get_exc_on=()):
        self.headers = {}
        self.cookies = {"csrftoken": "cookie-tok"}
        self.pages = pages or {}
        self.post_resp = post_resp
        self.post_exc = post_exc
        self.get_exc_on = get_exc_on
        self.calls = []

    def get(self, url, **kw):
        self.calls.append(("get", url, kw))
        if url in self.get_exc_on:
            raise RuntimeError("boom")
        r = self.pages.get(url, Resp())
        return r

    def post(self, url, **kw):
        self.calls.append(("post", url, kw))
        if self.post_exc:
            raise RuntimeError("boom")
        return self.post_resp or Resp()


LOGIN_HTML = "<input name='csrfmiddlewaretoken' value='TOK123'>"


def _install(monkeypatch, sa, fake):
    monkeypatch.setattr(sa.requests, "Session", lambda: fake)


def test_num_parsing(sa):
    assert sa._num("1,234.5") == 1234.5
    assert sa._num(" 12.5 % ") == 12.5
    assert sa._num(0) == 0.0
    assert sa._num("abc") is None
    assert sa._num(None) is None


def test_load_dotenv_sets_missing_only(sa, monkeypatch, tmp_path):
    (tmp_path / ".env").write_text(
        "# comment\n\nSCREENER_EMAIL=\"a@b.invalid\"\nNOEQUALS\nSCREENER_PASSWORD='pw1'\n"
        "EXISTING=new\n=novalue\n"
    )
    monkeypatch.setenv("EXISTING", "old")
    monkeypatch.delenv("SCREENER_EMAIL", raising=False)
    sa._load_dotenv()
    import os

    assert os.environ["SCREENER_EMAIL"] == "a@b.invalid"
    assert os.environ["SCREENER_PASSWORD"] == "pw1"
    assert os.environ["EXISTING"] == "old"
    monkeypatch.delenv("SCREENER_EMAIL")
    monkeypatch.delenv("SCREENER_PASSWORD")


def test_load_dotenv_no_file_is_noop(sa):
    sa._load_dotenv()  # no .env in tmp_path: must not raise


def test_session_requires_credentials(sa):
    with pytest.raises(sa.AuthError, match="SCREENER_EMAIL"):
        sa.session()


def test_session_requires_both_credentials(sa, monkeypatch):
    monkeypatch.setenv("SCREENER_EMAIL", DUMMY_EMAIL)
    with pytest.raises(sa.AuthError):
        sa.session()


def test_session_no_csrf_token(sa, monkeypatch):
    monkeypatch.setenv("SCREENER_EMAIL", DUMMY_EMAIL)
    monkeypatch.setenv("SCREENER_PASSWORD", DUMMY_PW)
    _install(monkeypatch, sa, FakeSession({sa.BASE + "/login/": Resp("<html>")}))
    with pytest.raises(sa.AuthError, match="CSRF"):
        sa.session()


def test_session_login_failure(sa, monkeypatch):
    monkeypatch.setenv("SCREENER_EMAIL", DUMMY_EMAIL)
    monkeypatch.setenv("SCREENER_PASSWORD", DUMMY_PW)
    fake = FakeSession({sa.BASE + "/login/": Resp(LOGIN_HTML), sa.BASE + "/": Resp("Welcome")})
    _install(monkeypatch, sa, fake)
    with pytest.raises(sa.AuthError, match="login failed"):
        sa.session()


@pytest.mark.parametrize("home", ["<a href='/logout/'>x</a>", "please LogOut here"])
def test_session_success_and_cached(sa, monkeypatch, home):
    monkeypatch.setenv("SCREENER_EMAIL", DUMMY_EMAIL)
    monkeypatch.setenv("SCREENER_PASSWORD", DUMMY_PW)
    fake = FakeSession({sa.BASE + "/login/": Resp(LOGIN_HTML), sa.BASE + "/": Resp(home)})
    _install(monkeypatch, sa, fake)
    s = sa.session()
    assert s is fake
    assert fake.headers["User-Agent"].startswith("Mozilla")
    post = [c for c in fake.calls if c[0] == "post"][0]
    assert post[2]["data"]["csrfmiddlewaretoken"] == "TOK123"
    assert post[2]["data"]["next"] == "/"
    assert post[2]["headers"]["Referer"] == sa.BASE + "/login/"
    assert sa.session() is s
    assert len([c for c in fake.calls if c[0] == "post"]) == 1


def _with_session(monkeypatch, sa, fake):
    monkeypatch.setattr(sa, "session", lambda: fake)


COMPANY = "https://www.screener.in/company/ABC/"


def test_export_no_link(sa, monkeypatch):
    _with_session(monkeypatch, sa, FakeSession({COMPANY: Resp("nothing")}))
    assert sa.company_export_bytes("ABC") is None


def test_export_post_ok_relative_url_and_form_token(sa, monkeypatch):
    html = "<form><button formaction='/user/company/export/42/'></button>" + LOGIN_HTML
    fake = FakeSession({COMPANY: Resp(html)}, post_resp=Resp(status=200, content=b"PKzipdata"))
    _with_session(monkeypatch, sa, fake)
    assert sa.company_export_bytes("ABC") == b"PKzipdata"
    post = [c for c in fake.calls if c[0] == "post"][0]
    assert post[1] == sa.BASE + "/user/company/export/42/"
    assert post[2]["data"] == {"csrfmiddlewaretoken": "TOK123"}
    assert post[2]["headers"]["Referer"] == COMPANY


def test_export_cookie_token_fallback_and_absolute_url(sa, monkeypatch):
    html = "<a href='https://x.invalid/company/export/7/'>e</a>"
    fake = FakeSession({COMPANY: Resp(html)}, post_resp=Resp(status=200, content=b"PK1"))
    _with_session(monkeypatch, sa, fake)
    assert sa.company_export_bytes("ABC") == b"PK1"
    post = [c for c in fake.calls if c[0] == "post"][0]
    assert post[1] == "https://x.invalid/company/export/7/"
    assert post[2]["data"]["csrfmiddlewaretoken"] == "cookie-tok"


def test_export_falls_back_to_get_when_post_fails(sa, monkeypatch):
    html = "<a href='/company/export/9/'>e</a>"
    url = sa.BASE + "/company/export/9/"
    fake = FakeSession(
        {COMPANY: Resp(html), url: Resp(status=200, content=b"PKget")}, post_exc=True
    )
    _with_session(monkeypatch, sa, fake)
    assert sa.company_export_bytes("ABC") == b"PKget"


def test_export_rejects_non_zip_or_bad_status(sa, monkeypatch):
    html = "<a href='/company/export/9/'>e</a>"
    url = sa.BASE + "/company/export/9/"
    fake = FakeSession(
        {COMPANY: Resp(html), url: Resp(status=200, content=b"<html>")},
        post_resp=Resp(status=403, content=b"PKxx"),
    )
    _with_session(monkeypatch, sa, fake)
    assert sa.company_export_bytes("ABC") is None


RATIO_HTML = """
<li><span class="name"> Market Cap </span> <span class="nowrap value">&#8377; <span class="number">1,23,456</span> Cr.</span></li>
<li><span class="name">Stock P/E</span><span class="value"><span class="number">21.5</span></span></li>
<li><span class="name">ROE</span><span class="value"><span class="number">15.2</span> %</span></li>
<li><span class="name">Unknown thing</span><span class="value">9</span></li>
<li><span class="name">ROCE</span><span class="value">n/a</span></li>
"""


def test_page_ratios_parses_known_keys_only(sa, monkeypatch):
    _with_session(monkeypatch, sa, FakeSession({COMPANY: Resp(RATIO_HTML)}))
    r = sa.page_ratios("ABC")
    assert r["pe"] == 21.5 and r["roe_page"] == 15.2
    assert "roce_page" not in r  # unparsable value skipped
    assert "market_cap_cr" not in r  # nested-span value is not plain numeric text


def test_page_ratios_market_cap_with_rupee_symbol(sa, monkeypatch):
    html = (
        '<span class="name">Market Cap</span><span class="value">₹ 500 Cr.</span>'
        '<span class="name">Current Price</span><span class="value">12</span>'
    )
    _with_session(monkeypatch, sa, FakeSession({COMPANY: Resp(html)}))
    r = sa.page_ratios("ABC")
    # "₹ 500 Cr." is not numeric -> skipped; price parsed
    assert "market_cap_cr" not in r
    assert r["current_price"] == 12.0


def test_page_ratios_fetch_failure_returns_empty(sa, monkeypatch):
    _with_session(monkeypatch, sa, FakeSession(get_exc_on=(COMPANY,)))
    assert sa.page_ratios("ABC") == {}


# ---- company_financials -------------------------------------------------------------------


def _sheet(rows):
    width = max(len(r) for r in rows)
    return pd.DataFrame([r + [None] * (width - len(r)) for r in rows], dtype=object)


def _patch_excel(monkeypatch, sa, df, names=("Data Sheet",), fail=False):
    class XL:
        sheet_names = list(names)

        def __init__(self, buf):
            if fail:
                raise ValueError("bad xlsx")

    seen = {}

    def read_excel(buf, sheet_name=None, header=None):
        seen["sheet"] = sheet_name
        return df

    monkeypatch.setattr(sa.pd, "ExcelFile", XL)
    monkeypatch.setattr(sa.pd, "read_excel", read_excel)
    return seen


FULL_ROWS = [
    ["Report", "x"],
    ["Net profit", 100, 80, 60],
    ["Sales", 1000, 900, 800, 700, 600, 500],
    ["Interest", 10],
    ["Profit before tax", 150],
    ["EPS in Rs", 10, 8],
    ["Borrowings", 200, 100],
    ["Reserves", 400, 300],
    ["Equity Capital", 100, 100],
    ["Other Liabilities", 50, 40],
    ["Net Block", 300, 250],
    ["Inventory", 20],
    ["Trade receivables", 30],
    ["Cash & Bank", 40],
    ["Cash from Operating Activity", 90],
    ["No. of Equity Shares", 10, 9],
    ["Dividend Amount", 5, 0, 3],
]


def test_company_financials_no_export(sa, monkeypatch):
    monkeypatch.setattr(sa, "company_export_bytes", lambda s: None)
    assert sa.company_financials("ABC") == {}


def test_company_financials_unparsable_workbook(sa, monkeypatch):
    monkeypatch.setattr(sa, "company_export_bytes", lambda s: b"PK")
    _patch_excel(monkeypatch, sa, None, fail=True)
    assert sa.company_financials("ABC") == {}


def test_company_financials_full(sa, monkeypatch):
    monkeypatch.setattr(sa, "company_export_bytes", lambda s: b"PK")
    monkeypatch.setattr(
        sa, "page_ratios", lambda s: {"market_cap_cr": 1000.0, "pe": 20.0, "roe_page": 12.5}
    )
    seen = _patch_excel(monkeypatch, sa, _sheet(FULL_ROWS), names=("Summary", "Data Sheet"))
    f = sa.company_financials("ABC")
    assert seen["sheet"] == "Data Sheet"
    assert f["source"] == "screener.in"
    assert f["net_income"] == 100 and f["net_income_prev"] == 80
    assert f["profit_growth"] == pytest.approx(25.0)
    assert f["eps_growth"] == pytest.approx(25.0)
    assert f["revenue"] == 1000 and f["revenue_5y_ago"] == 500
    assert f["ebit"] == 160
    assert f["roe"] == 12.5  # page ratio wins
    assert f["roe_history"] == [20.0, 20.0]
    # equity 500, assets 500+200+50
    assert f["roa"] == round(100 / 750 * 100, 1)
    assert f["roa_prev"] == round(80 / 540 * 100, 1)
    assert f["debt_to_equity"] == 0.4
    assert f["debt_to_assets"] == round(200 / 750, 2)
    assert f["asset_turnover"] == round(1000 / 750, 2)
    assert f["dividend_pay_years"] == 2
    assert f["dividend_history"] == [5, 0, 3]
    assert f["enterprise_value"] == 1000 + 200 - 40
    assert f["earnings_yield"] == round(160 / 1160 * 100, 2)
    assert f["debt_history"] == [200, 100] and f["capex_history"] == [300, 250]
    assert f["pe"] == 20.0 and f["shares"] == 10 and f["shares_prev"] == 9
    assert f["cash"] == 40 and f["inventory"] == 20 and f["receivables"] == 30
    assert "roce" not in f  # None values dropped


def test_company_financials_falls_back_to_first_sheet_and_computed_roe(sa, monkeypatch):
    monkeypatch.setattr(sa, "company_export_bytes", lambda s: b"PK")
    monkeypatch.setattr(sa, "page_ratios", lambda s: {})
    rows = [
        ["Net profit", 50],
        ["Reserves", 150],
        ["Equity Capital", 50],
        ["Sales", 400],
        ["Borrowings", 20],
    ]
    seen = _patch_excel(monkeypatch, sa, _sheet(rows), names=("Alpha", "Beta"))
    f = sa.company_financials("ABC")
    assert seen["sheet"] == "Alpha"
    assert f["roe"] == 25.0
    assert f["debt_to_equity"] == 0.1
    assert f["revenue_5y_ago"] == 400  # short series -> last element
    assert "enterprise_value" not in f and "earnings_yield" not in f
    assert "profit_growth" not in f  # no prior-year net profit


def test_company_financials_zero_prior_and_empty(sa, monkeypatch):
    monkeypatch.setattr(sa, "company_export_bytes", lambda s: b"PK")
    monkeypatch.setattr(sa, "page_ratios", lambda s: {})
    rows = [["Net profit", 5, 0], ["Dividend Paid", 0, 0]]
    _patch_excel(monkeypatch, sa, _sheet(rows))
    f = sa.company_financials("ABC")
    assert "profit_growth" not in f  # division by a zero base is skipped
    assert "dividend_pay_years" not in f
    assert f["quarterly_net_income"] == [5.0, 0.0]


# ---- fundamentals_batch -------------------------------------------------------------------


def test_fundamentals_batch_isolates_failures_and_sleeps(sa, monkeypatch, capsys):
    sleeps = []
    monkeypatch.setattr(sa.time, "sleep", sleeps.append)

    def fin(sym):
        if sym == "BAD":
            raise RuntimeError("x")
        return {} if sym == "EMPTY" else {"k": sym}

    monkeypatch.setattr(sa, "company_financials", fin)
    out = sa.fundamentals_batch(["A", "BAD", "EMPTY"], pause=0.5, verbose=True)
    assert out == {"A": {"k": "A"}}
    assert sleeps == [0.5, 0.5, 0.5]
    assert capsys.readouterr().out == ""  # progress prints only every 25th


def test_fundamentals_batch_progress_every_25(sa, monkeypatch, capsys):
    monkeypatch.setattr(sa.time, "sleep", lambda s: None)
    monkeypatch.setattr(sa, "company_financials", lambda s: {"k": 1})
    syms = [f"S{i}" for i in range(25)]
    sa.fundamentals_batch(syms)
    assert capsys.readouterr().out.strip() == "25/25 fetched (25 ok)"
    sa.fundamentals_batch(syms, verbose=False)
    assert capsys.readouterr().out == ""


def test_fundamentals_batch_reraises_auth_error(sa, monkeypatch):
    monkeypatch.setattr(sa.time, "sleep", lambda s: None)

    def fin(sym):
        raise sa.AuthError("nope")

    monkeypatch.setattr(sa, "company_financials", fin)
    with pytest.raises(sa.AuthError):
        sa.fundamentals_batch(["A"])


def test_main_block_auth_error(sa, monkeypatch, capsys):
    import runpy
    import sys

    monkeypatch.delitem(sys.modules, "screener_in_auth", raising=False)
    monkeypatch.setattr(sys, "argv", ["screener_in_auth.py", "TCS", "bad-arg"])
    monkeypatch.delenv("SCREENER_EMAIL", raising=False)
    monkeypatch.delenv("SCREENER_PASSWORD", raising=False)
    import pathlib

    src = pathlib.Path(sa.__file__).name  # tmp path; use repo module path instead
    repo = pathlib.Path(__file__).resolve().parent.parent / "screener_in_auth.py"
    # a stray .env next to the real module could supply credentials; neutralise loader
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: False)
    runpy.run_path(str(repo), run_name="__main__")
    out = capsys.readouterr().out
    assert out.startswith("AUTH: set SCREENER_EMAIL")
    assert src  # keep linter quiet about unused


def test_main_block_success(sa, monkeypatch, capsys):
    import pathlib
    import runpy
    import sys

    monkeypatch.setattr(sys, "argv", ["screener_in_auth.py", "TCS", "bad-arg"])
    monkeypatch.setenv("SCREENER_EMAIL", DUMMY_EMAIL)
    monkeypatch.setenv("SCREENER_PASSWORD", DUMMY_PW)
    fake = FakeSession(
        {
            sa.BASE + "/login/": Resp(LOGIN_HTML),
            sa.BASE + "/": Resp("logout"),
            sa.BASE + "/company/TCS/": Resp("nothing"),
        }
    )
    monkeypatch.setattr(sa.requests, "Session", lambda: fake)
    monkeypatch.setattr(pathlib.Path, "exists", lambda self: False)
    repo = pathlib.Path(__file__).resolve().parent.parent / "screener_in_auth.py"
    runpy.run_path(str(repo), run_name="__main__")
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("logged in")
    assert lines[1] == "TCS: export bytes = 0"
    assert lines[2] == "TCS: (empty — export not found/parsed)"
    assert len(lines) == 3  # 'bad-arg' filtered by isalnum


@pytest.mark.xfail(
    strict=True,
    raises=TypeError,
    reason="company_financials divides g(debt)/g(eq) with no Borrowings row (None / float)",
)
def test_company_financials_without_borrowings_row_should_not_crash(sa, monkeypatch):
    monkeypatch.setattr(sa, "company_export_bytes", lambda s: b"PK")
    monkeypatch.setattr(sa, "page_ratios", lambda s: {})
    rows = [["Net profit", 50], ["Reserves", 150], ["Equity Capital", 50]]
    _patch_excel(monkeypatch, sa, _sheet(rows))
    f = sa.company_financials("ABC")
    assert f["net_income"] == 50


@pytest.mark.parametrize("which", ["email", "password"])
def test_session_with_only_one_credential_never_opens_a_session(sa, monkeypatch, which):
    monkeypatch.setenv("SCREENER_EMAIL" if which == "email" else "SCREENER_PASSWORD", "dummy-x")
    fake = FakeSession({})
    _install(monkeypatch, sa, fake)
    with pytest.raises(sa.AuthError, match="SCREENER_EMAIL"):
        sa.session()
    assert fake.calls == []


def test_earnings_yield_needs_both_ebit_and_market_cap(sa, monkeypatch):
    monkeypatch.setattr(sa, "company_export_bytes", lambda s: b"PK")
    with_ebit = [["Net profit", 50], ["Profit before tax", 150], ["Interest", 10]]
    no_ebit = [["Net profit", 50]]
    _patch_excel(monkeypatch, sa, _sheet(with_ebit), names=("Data Sheet",))
    monkeypatch.setattr(sa, "page_ratios", lambda s: {})
    assert "earnings_yield" not in sa.company_financials("ABC")  # ebit but no market cap
    _patch_excel(monkeypatch, sa, _sheet(no_ebit), names=("Data Sheet",))
    monkeypatch.setattr(sa, "page_ratios", lambda s: {"market_cap_cr": 1000.0})
    assert "earnings_yield" not in sa.company_financials("ABC")  # market cap but no ebit
