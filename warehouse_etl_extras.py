#!/usr/bin/env python3
"""ETL for the non-OHLCV warehouse domains: cgd / energy / audit.

Companion to the ohlcv warehouse (see warehouse/ohlcv/, commit b8473d0).
Each domain follows the same pattern: canonical source -> zstd parquet under
warehouse/<domain>/ -> view registered in warehouse/warehouse.duckdb.

Canonical sources (never edit the parquet by hand; re-run this):
  cgd.ga_allotment      <- ~/repos/cng-cgd-retail-outlet-mapping/data/cgd_ga_allotment.csv
  cgd.retail_outlets    <- ~/repos/cng-cgd-retail-outlet-mapping/data/outlets_cgd.csv.gz
  energy.gas_demand     <- PNGRB "2030/2040 Natural Gas Demand Projections" (national,
                           sector-wise; recovered from the 2026-06-19 claude.ai analysis
                           of the report — the report has NO state-wise demand table,
                           GA-level infrastructure is the granular path)
  audit.lfs_inventory   <- ~/repos/repo-data-dedup/audit/lfs_inventory.csv
  audit.repo_summary    <- ~/repos/repo-data-dedup/audit/repo_summary.csv

These tables are small (<5 MB) and are committed as REGULAR git objects —
warehouse/{cgd,energy,audit} are excluded from the repo's LFS patterns on purpose
(account LFS budget exhaustion makes new LFS objects unreachable to cloners).
"""
from pathlib import Path

import duckdb

HOME = Path.home()
WH = HOME / "repos/global-market-data/warehouse"
CGD_REPO = HOME / "repos/cng-cgd-retail-outlet-mapping/data"
AUDIT_REPO = HOME / "repos/repo-data-dedup/audit"

# PNGRB 2030/2040 demand projections, mmscmd. GtG = Good-to-Go, GtB = Good-to-Best.
GAS_DEMAND = [
    # sector, fy24, y2030_gtg, y2030_gtb, y2040_gtg, y2040_gtb
    ("CGD",           36.9,  87.1, 126.1, 216.4, 270.8),
    ("Power",         25.2,  35.7,  40.0,  43.5,  52.8),
    ("Refinery",      22.0,  43.4,  50.9,  52.4,  57.8),
    ("Fertilizer",    58.0,  65.3,  69.3,  72.9,  80.5),
    ("Steel",          3.2,   4.3,   5.1,   6.4,   9.3),
    ("LNG Transport",  0.0,   3.9,   6.6,  26.3,  65.7),
    ("Others",        42.0,  57.3,  66.6,  76.9,  93.3),
]


def main():
    for d in ("cgd", "energy", "audit"):
        (WH / d).mkdir(parents=True, exist_ok=True)

    con = duckdb.connect(str(WH / "warehouse.duckdb"))

    def load(view, sql, out):
        con.execute(f"COPY ({sql}) TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)")
        con.execute(f"CREATE OR REPLACE VIEW {view} AS SELECT * FROM read_parquet('{out}')")
        n = con.execute(f"SELECT count(*) FROM {view}").fetchone()[0]
        print(f"{view:22s} {n:>8,} rows  <- {out.name}")

    load("cgd_ga_allotment",
         f"SELECT * FROM read_csv_auto('{CGD_REPO}/cgd_ga_allotment.csv', header=true)",
         WH / "cgd/ga_allotment.parquet")

    load("cgd_retail_outlets",
         f"SELECT * FROM read_csv_auto('{CGD_REPO}/outlets_cgd.csv.gz', header=true)",
         WH / "cgd/retail_outlets.parquet")

    con.execute("CREATE OR REPLACE TEMP TABLE _gd (sector VARCHAR, fy24 DOUBLE,"
                " y2030_gtg DOUBLE, y2030_gtb DOUBLE, y2040_gtg DOUBLE, y2040_gtb DOUBLE)")
    con.executemany("INSERT INTO _gd VALUES (?,?,?,?,?,?)", GAS_DEMAND)
    load("gas_demand_projections",
         "SELECT *, 'PNGRB 2030/2040 Natural Gas Demand Projections (national, mmscmd)'"
         " AS source FROM _gd",
         WH / "energy/gas_demand_projections.parquet")

    load("lfs_inventory",
         f"SELECT * FROM read_csv_auto('{AUDIT_REPO}/lfs_inventory.csv', header=true)",
         WH / "audit/lfs_inventory.parquet")

    load("lfs_repo_summary",
         f"SELECT * FROM read_csv_auto('{AUDIT_REPO}/repo_summary.csv', header=true)",
         WH / "audit/repo_summary.parquet")

    # bhavcopy domain: official NSE/BSE daily bhavcopy store (Jun 2025 ->).
    # Canonical source is ~/data/bhavcopy.duckdb (maintained by bhavcopy_store.py /
    # the home-repo collector) — that file lives OUTSIDE any pushed repo and the
    # Downloads tree it caches to is wipe-prone, hence this warehoused snapshot.
    # Unique vs ohlcv_in: full BSE coverage (~8.8k symbols) + official raw fields
    # (ISIN, series). cleaned_ohlcv is the collision-safe NSE+BSE merge — NSE and
    # BSE share bare symbols (~2.5k collide); never union the raws by symbol alone.
    bhav_src = HOME / "data/bhavcopy.duckdb"
    if bhav_src.exists():
        (WH / "bhavcopy").mkdir(exist_ok=True)
        con.execute(f"ATTACH '{bhav_src}' AS bhav (READ_ONLY)")
        for tbl, datecol in [("nse_raw", "TradDt"), ("bse_raw", "TradDt"),
                             ("cleaned_ohlcv", "trade_date")]:
            years = [r[0] for r in con.execute(
                f"SELECT DISTINCT year({datecol}) FROM bhav.{tbl} ORDER BY 1").fetchall()]
            outs = []
            for y in years:
                out = WH / f"bhavcopy/{tbl}.year={y}.parquet"
                con.execute(f"COPY (SELECT * FROM bhav.{tbl} WHERE year({datecol})={y})"
                            f" TO '{out}' (FORMAT PARQUET, COMPRESSION ZSTD)")
                outs.append(out)
            glob = WH / f"bhavcopy/{tbl}.year=*.parquet"
            con.execute(f"CREATE OR REPLACE VIEW bhavcopy_{tbl.replace('_ohlcv','')} AS "
                        f"SELECT * FROM read_parquet('{glob}')")
            n = con.execute(f"SELECT count(*) FROM bhavcopy_{tbl.replace('_ohlcv','')}").fetchone()[0]
            print(f"{'bhavcopy_'+tbl.replace('_ohlcv',''):22s} {n:>8,} rows  ({len(outs)} year files)")
        con.execute("DETACH bhav")
    else:
        print("bhavcopy: source store not found, skipped (views left as-is)")

    # derived view: per-entity territory + retail/CNG footprint.
    # Join on state AND district — district names collide across states
    # (Aurangabad MH/BR, Bilaspur CG/HP, Hamirpur UP/HP, ...).
    con.execute("""
        CREATE OR REPLACE VIEW entity_cng_footprint AS
        SELECT a.entity,
               count(DISTINCT a.state || '|' || a.district)  AS districts,
               count(DISTINCT a.ga_id)                       AS gas,
               count(DISTINCT o.outlet_id)                   AS outlets,
               count(DISTINCT CASE WHEN o.has_cng THEN o.outlet_id END)
                                                             AS cng_outlets,
               round(count(DISTINCT CASE WHEN o.has_cng THEN o.outlet_id END) * 100.0
                     / nullif(count(DISTINCT o.outlet_id), 0), 1)
                                                             AS cng_pct
        FROM cgd_ga_allotment a
        LEFT JOIN cgd_retail_outlets o
               ON o.state = a.state AND o.district = a.district
        GROUP BY a.entity
        ORDER BY districts DESC
    """)
    n = con.execute("SELECT count(*) FROM entity_cng_footprint").fetchone()[0]
    print(f"{'entity_cng_footprint':22s} {n:>8,} rows  (derived view)")

    # cross-domain sanity: outlets whose district has a CGD GA
    n_link = con.execute(
        "SELECT count(*) FROM cgd_retail_outlets WHERE cgd_status = 'CGD GA authorised'"
    ).fetchone()[0]
    print(f"\nsanity: outlets linked to an authorised CGD GA: {n_link:,}")
    con.close()


if __name__ == "__main__":
    main()
