# SUPERSEDED — use ../../warehouse/

These monolithic panels are replaced (2026-07-22) by `warehouse/ohlcv/<MARKET>/`
— year-partitioned zstd parquet + `warehouse/warehouse.duckdb` (views).

Why: the monoliths were duplicated across two repos (~900MB where ~450MB of
canonical data exists), the copies diverged, THIS repo's US.parquet is the
known-broken interrupted alphabetical collection, and every daily update
re-uploaded a 68-184MB LFS object. The warehouse updates one ~8MB year file.

Local .parquet files here are untracked caches; they may be deleted freely.
