# CLAUDE.md — global-market-data

## What this repo is

Deep, multi-market historical equity data (10 years, 20 markets, ~44M OHLCV rows) plus the
**point-in-time, no-lookahead backtest engine** that turns it into evidence on which screens
actually work, where. Sibling repo to [`global-stock-screener`](https://github.com/herrrickshaw/global-stock-screener)
(same strategy library, ~1yr cached data, optimized for a fast clone and *today's* results) — use
that repo for live screening, this one to check whether a screen has a real historical edge first.

> ⚠️ Educational / research only. NOT investment advice. Backtest edges are historical averages,
> not guarantees.

## Headline finding (`BACKTEST_FINDINGS.md`, 10-year point-in-time, 21-day forward)

1. **`rsi_oversold` (mean-reversion)** is the most robust screen — positive edge in every market
   tested, #1 in US/KR/CN/JP.
2. **`golden_crossover`** is consistently the weakest — negative/near-zero edge everywhere.
3. **India is momentum-friendly** (`near_high` +1.33pp, `momentum` +1.18pp) while the
   **US/KR/CN lean mean-reversion** — the edge is regime/market-dependent, not universal.

Full ranked table and methodology in `BACKTEST_FINDINGS.md`. Data store is LFS-free as of the
2026-07-22 rebuild — regular git objects, not LFS pointers.

## Key docs (read before assuming, don't duplicate here)

| Doc | What |
|---|---|
| `ARCHITECTURE.md` / `DATA_SCHEMA_AND_ARCHITECTURE.md` | system + schema design |
| `DATA_AND_MODULES.md` | module map |
| `MEMORY_ARCHITECTURE.md` | this repo's own PIT/lookahead-safety design (unrelated to Claude memory — see below) |
| `HOW_IT_WORKS.md` / `QUICK_START.md` | onboarding |
| `MODEL_ENHANCEMENT_ANALYSIS.md` / `F1_TUNING_METHODOLOGY_REPORT.md` | scoring methodology |

## Cross-session project memory

A Claude Code session rooted here has no persisted memory of its own — and note `MEMORY_ARCHITECTURE.md`
in this repo is about the *backtest engine's* point-in-time data design, not Claude's memory system.
Decisions, hazards, and credential locations for the broader market-research platform live under
the `~` (home) session instead: `~/.claude/projects/-Users-umashankar/memory/`, indexed by
`MEMORY.md` and mapped by the `reference_memory_map` memory (see `project_piotroski_backtests`,
`reference_deep_10y_market_data` for prior findings on this exact dataset). From here, reach it
with `qmd search "<topic>"` (indexes that memory directory regardless of working directory) rather
than assuming this session has its own history. For fast orientation, a published snapshot of 128
incident-sourced rules from across that memory is browsable at
https://claude.ai/code/artifact/e29ceaeb-099e-4de7-ab43-dcd97a2d8f57 ("Ways of Working") — a
starting point, not authoritative over the live memory files.

<!-- source-fetch-toolkit:v1 (managed by ~/bin/add_source_fetch_section.py — edit there, not here) -->
## Source fetching for data collection & literature surveys

When a task needs a primary document (statute, regulator data, government release, paper, filing),
fetch the document itself — never cite a search snippet as if it had been read.

- **On the owner's Mac:** `fetch_source.py "<url | doi:… | celex:… | arxiv:…>" --out ./sources`
  (in `~/bin`; full guide `~/bin/SOURCE_FETCH_TOOLKIT.md`). Scheduled/unattended runs use only
  `bash /Users/umashankar/bin/fetch_source.sh "<target>"` (allowlisted) and then Read the `.txt` it reports.
- **Elsewhere (cloud sessions, CI):** follow the same ladder by hand — direct GET with a browser User-Agent →
  headless browser only if the page needs JavaScript → official alternates (DOI → OpenAlex
  `api.openalex.org/works/doi:<doi>` locations → repository copy; EUR-Lex → EU Publications Office
  `publications.europa.eu/resource/celex/<CELEX>`; arXiv pdf/abs) → Wayback Machine `id_` snapshot.
- **Hard rule:** a CAPTCHA, robot check, Cloudflare/Akamai/WAF challenge or login wall means stop on that
  host. Do not bypass it (no TLS impersonation, stealth browsers, identity rotation or challenge solving);
  look for the same document via another route, or report it as unavailable.
- **Record** for every figure: URL read, title/date, page/table, fetch route; mark abstract-only or
  snippet-only sources. Fetch politely (~1 request/second per host, no bulk crawling).
<!-- /source-fetch-toolkit -->
