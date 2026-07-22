# Data Schema & Architecture Map

End-to-end map of the global stock screener: how data flows from raw exchange
feeds through the tiered memory, linkage, serving, ML and CRUD-pipeline layers to
the outputs. (GitHub renders the Mermaid diagrams below.)

> ⚠️ Educational / research only. NOT investment advice.

---

## 1. System architecture (layers)

```mermaid
flowchart TB
    subgraph ING["① Ingestion (official / free feeds)"]
        A1["NSE/BSE bhavcopy\n(bhavcopy_history)"]
        A2["Yahoo → Stooq\n(data_sources)"]
        A3["SEC EDGAR\n(sec_fundamentals)"]
        A4["Screener.in public + auth\n(screener_in / _auth / public_screens)"]
        A5["Exchange universes\n(universe_sources)"]
    end

    subgraph STORE["② Tiered storage (Lambda: batch + speed)"]
        B1["LTM 5y — batch\ncache_seed/ltm/&lt;MKT&gt;.parquet"]
        B2["STM 1y — speed\ncache_seed/cleaned_long*.parquet"]
        B3["LMDB NoSQL store\nohlcv.lmdb (symbol→bytes)"]
        B4["Reference\nreference_seed/*"]
    end

    subgraph LINK["③ Linkage (datalink)"]
        C1["data_manifest.json\n(single index)"]
        C2["memoized load_market()"]
        C3["incremental store build\n(.store_state.json)"]
        C4["ccc_map_cached()"]
    end

    subgraph SERVE["④ Serving layer (denormalised, precomputed)"]
        D1["serving/&lt;MKT&gt;.parquet\nSMA/RSI/returns/turnover/tier"]
        D2["CDC delta log\ncdc/&lt;MKT&gt;.parquet"]
    end

    subgraph SCREEN["⑤ Screening & ML"]
        E1["strategies/ (11)\n+ screener_kit"]
        E2["screen_metrics\n(price query formulas)"]
        E3["fundamental_metrics\n(Piotroski/ROCE/…)"]
        E4["validation\n(popular screens)"]
        E5["ml_supervised (L1)\nauto_screener (L2)\nrl_trader (L3)"]
    end

    subgraph PIPE["⑥ CRUD pipeline (filter-driven tiering)"]
        F1["pipeline_state.json\n(promotion registry)"]
        F2["sync / promote / demote / refresh"]
    end

    subgraph OUT["⑦ Outputs"]
        G1["run.py / results CSV"]
        G2["daily mailer\n(build_mailer / send_mailer)"]
        G3["discovered_screens/*.json"]
    end

    ING --> STORE --> LINK --> SERVE --> SCREEN --> OUT
    SERVE --> PIPE
    SCREEN --> PIPE
    PIPE -->|deep-track promoted only| STORE
    A3 --> E3
    A4 --> E4
    B4 --> E5
    F2 -->|fundamentals for watchlist| A3
```

---

## 2. Data flow (daily pipeline)

```mermaid
sequenceDiagram
    autonumber
    participant Cal as market_holidays
    participant Bhav as update_bhavcopy_daily
    participant Mem as market_memory
    participant Serve as serving_layer
    participant Pipe as pipeline
    participant Out as mailer/outputs

    Cal->>Mem: skip markets closed today
    Bhav->>Bhav: fetch new NSE/BSE bar → cleaned_long (IN)
    Bhav->>Serve: incremental LMDB store (changed only)
    Mem->>Mem: UPSERT new bars → LTM(5y) → derive STM(1y)
    Serve->>Serve: rebuild serving views + CDC deltas (changed only)
    Pipe->>Pipe: sync() run filters → promote/demote watchlist
    Pipe->>Pipe: refresh() fetch fundamentals for PROMOTED only
    Serve->>Out: screens / ML / mailer read precomputed views
```

**CRUD tiering rule:** STM (1y) is kept for the **whole universe** (cheap); LTM (5y)
depth + expensive fundamentals are maintained **only for promoted (filter-clearing)
stocks**, bounding fetch/compute time. See `MEMORY_ARCHITECTURE.md`, `DATA_LINKAGE.md`.

---

## 3. Data schemas

### 3.1 OHLCV long frame — LTM & STM seeds
`cache_seed/ltm/<MKT>.parquet`, `cache_seed/cleaned_long[_MKT].parquet`

| column | type | notes |
|--------|------|-------|
| Symbol | str | bare ticker (NSE precedence on collisions) |
| Date | datetime64 | trading day |
| Open, High, Low, Close | float32 | prices |
| Volume | int64 | shares |

*Grain:* one row per (Symbol, Date). LTM = trailing 5y, STM = trailing 1y slice of LTM.

> **Storage note (dedup):** only the **LTM is committed** — the STM seeds
> (`cleaned_long*.parquet`) are 100% contained in the LTM, so they are **derived on
> first use** via `market_memory.ensure_stm_seeds()` rather than committed. This
> removed ~120 MB of duplicated data from the repo.

### 3.2 LMDB store — `ohlcv.lmdb`
| key | value |
|-----|-------|
| `<symbol>` (utf-8) | zstd Arrow IPC of that symbol's Date-indexed OHLCV frame |
| `__meta__` | `"{n_symbols}|{max_date}"` |

### 3.3 Serving view (denormalised) — `cache_seed/serving/<MKT>.parquet`
One row per symbol; precomputed at write-time so screening is a vectorised filter.

| column | meaning |
|--------|---------|
| Symbol, Market, Close, Bars, LastDate | identity |
| SMA20, SMA50, SMA200 | moving averages |
| RSI14 | Wilder RSI |
| High252, Low252, PctFromHigh, PctFromLow | 52-week extremes |
| Ret21, Ret63, Ret126, Ret252 | horizon returns % |
| Above200DMA, GoldenCross | trend flags |
| TurnoverLocal, Turnover_USD, Liquidity | liquidity (High/Medium/Low tier) |

### 3.4 CDC delta log — `cache_seed/cdc/<MKT>.parquet`
Blueprint delta model (Operation / Key / Value-Timestamp).

| column | meaning |
|--------|---------|
| op | INSERT / UPDATE |
| key | Symbol |
| value | Close at capture |
| asof | last bar date |
| ts | capture timestamp (UTC) |

### 3.5 Fundamentals cache — `cache_seed/fundamentals/<MKT>.parquet`
Per-symbol financials (US = SEC EDGAR, IN = screener.in auth).

| column (subset) | meaning |
|-----------------|---------|
| Symbol, source | identity / provenance |
| net_income(+_prev), revenue, cfo, ebit | income & cash |
| roe, roa(+_prev), debt_to_equity, current_ratio(+_prev) | ratios |
| gross_margin(+_prev), asset_turnover(+_prev), shares(+_prev) | Piotroski inputs |
| eps_growth, capex_history, dividend_history | growth / payout |

Derived at read time by `fundamental_metrics._enrich`: `piotroski` (0–9), `roce`,
`earnings_yield`, `fcf_yield`, `div_yield`.

### 3.6 Public popular screens — `cache_seed/public_screens/<key>.parquet`
Cached membership of Screener.in curated screens (Symbol + screen columns). 26 screens.

### 3.7 Promotion registry — `cache_seed/pipeline_state.json` *(gitignored)*
```json
{ "IN": { "promoted": { "WHEELS": { "filters": ["companies_creating_new_high",
  "multibagger_momentum", "popular"], "since": "2026-07-01",
  "refreshed": "2026-07-01" } }, "synced": "2026-07-01T13:54:14" } }
```

### 3.8 ML models — `cache_seed/models/<MKT>.pkl`, `<MKT>_rl.pkl` *(gitignored)*
| file | contents |
|------|----------|
| `<MKT>.pkl` | supervised classifier + feature list + effective horizon |
| `<MKT>_rl.pkl` | Q-table (state→8 action values) + action list |

### 3.9 Data index — `cache_seed/data_manifest.json` *(gitignored)*
Per-asset: market, tier, path, symbols, rows, date span, signature (size:mtime).

---

## 4. Entity relationships

```mermaid
erDiagram
    UNIVERSE ||--o{ OHLCV_LTM : "5y history"
    OHLCV_LTM ||--|| OHLCV_STM : "trailing 1y slice"
    OHLCV_STM ||--|| SERVING : "precomputed features"
    OHLCV_STM ||--|| LMDB : "per-symbol bytes"
    SERVING ||--o{ CDC : "row deltas"
    SERVING ||--o{ SCREEN_RESULT : "price screens"
    FUNDAMENTALS ||--o{ SCREEN_RESULT : "fundamental screens"
    SCREEN_RESULT ||--o{ WATCHLIST : "clears >= N filters"
    WATCHLIST ||--o{ FUNDAMENTALS : "deep-fetch promoted only"
    WATCHLIST ||--o{ OHLCV_LTM : "deep-track promoted only"
    SERVING ||--o{ MODEL : "features → labels"
    MODEL ||--o{ SCREEN_RESULT : "supervised/RL signals"
```

---

## 5. Module → layer map

| Layer | Modules |
|-------|---------|
| Ingestion | `bhavcopy_history`, `bhavcopy_store`, `data_sources`, `sec_fundamentals`, `screener_in`, `screener_in_auth`, `public_screens`, `universe_sources`, `market_calendar`, `market_holidays` |
| Storage | `market_memory` (LTM/STM), `bhavcopy_store` (LMDB), `frames` (OHLCV helpers), `reference_data` |
| Linkage | `datalink` (manifest, memo, incremental store, CCC cache) |
| Serving | `serving_layer` (materialised views + CDC) |
| Screening | `strategies/`, `screener_kit`, `custom_screener`, `liquidity`, `screen_metrics`, `fundamental_metrics`, `validation` |
| ML | `ml_supervised` (L1), `auto_screener` (L2 unsupervised + RL correction), `rl_trader` (L3 PPO/Q), `pattern_discovery`, `ml_signal_engine` |
| Pipeline | `pipeline` (CRUD watchlist), `daily_memory.sh`, `update_bhavcopy_daily`, `daily_pipeline.sh` |
| Outputs | `run.py`, `build_mailer`, `send_mailer`, `run_global_analysis`, `market_performance` |

See also: `ARCHITECTURE.md`, `MEMORY_ARCHITECTURE.md`, `DATA_LINKAGE.md`,
`AUTO_SCREENER.md`, `DATA_AND_MODULES.md`.
