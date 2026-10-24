# Decision Log

New decisions are appended; superseded entries stay for history.

| ID | Date | Decision | Rationale and consequences |
|----|------|----------|----------------------------|
| D001 | 2026-10-24 | Python 3.11 minimum; 3.12 permitted | Required by the project brief and compatible with pandas/numpy. `.python-version` pins the local dev interpreter to 3.11. |
| D002 | 2026-10-24 | `src/` layout, setuptools build backend, uv + `uv.lock` | Editable installs, reproducible dependency resolution. Plain `pip install -e ".[dev]"` remains supported. |
| D003 | 2026-10-24 | TOML config via stdlib `tomllib`; reject unknown keys | No extra dependency; typos fail loudly instead of silently falling back to defaults. |
| D004 | 2026-10-24 | pandas for tabular data | Familiar research ecosystem; revisit Polars only if profiling shows a real need. |
| D005 | 2026-10-24 | Frozen dataclasses, not pydantic, for data contracts | Zero dependency and explicit validation. Reassess if external schema export/validation becomes necessary. |
| D006 | 2026-10-24 | Timezone-aware UTC datetimes; separate `observed_at`/`as_of` and `available_at` | Makes point-in-time correctness explicit at the type boundary. Contracts reject look-ahead ordering and naive datetimes. |
| D007 | 2026-10-24 | Stable `security_id` is the identity; ticker is an attribute | Tickers change and are reused; historical ticker mapping remains a Phase 2 responsibility. |
| D008 | 2026-10-24 | CSV for synthetic data in Phase 1 | Small, human-inspectable, dependency-free. Parquet/DuckDB deferred to Phase 2 when real datasets justify them. |
| D009 | 2026-10-24 | stdlib `argparse` CLI; no web API | Keeps the research pipeline scriptable and avoids premature frameworks. |
| D010 | 2026-10-24 | Explicit seeded `numpy.random.Generator` (`seeding.make_rng`) | Avoids hidden global RNG state so synthetic data and experiments are reproducible. |
| D011 | 2026-10-24 | Synthetic calendar is weekdays only; no US holidays | Sufficient for infrastructure tests; real trading calendars are a Phase 2/3 concern. Documented as a limitation. |
| D012 | 2026-10-24 | Do not enforce a label/benchmark arithmetic identity in `Label` | Return conventions (total return, excess return definition) are not finalized. The contract records both fields; definitions arrive with Phase 3. |
| D013 | 2026-10-24 | GitHub Actions CI on every push and pull request: Ubuntu + Windows, Python 3.11, `uv sync --locked` | Verifies the same commands developers run locally. Windows is included because it is the primary development environment. The workflow is defined but has not run remotely until the repository is pushed. |
| D014 | 2026-10-24 | Provider-agnostic ingestion interface with CSV and Tiingo adapters | CSV ingests licensed bulk exports offline; Tiingo validates a real HTTP adapter without requiring a paid plan. EODHD/Sharadar/Norgate/CRSP remain candidates behind the same interface. See `docs/DATA_SOURCES.md`. |
| D015 | 2026-10-24 | Raw payloads retained per run; normalized Parquet holds the latest revision | Raw snapshots preserve provenance and checksums. Re-ingestion deduplicates on `(security_id, date)`, so it cannot silently duplicate rows. Bitemporal vintage reconstruction is not implemented. |
| D016 | 2026-10-24 | Require `adj_close`; store raw OHLCV as reported | Labels must use split/dividend-adjusted closes. Keeping raw OHLCV and the adjustment separate avoids silently changing the provider's reported prices. |
| D017 | 2026-10-24 | Provisional `security_id = provider:symbol` unless the source supplies an id | Ticker reuse and renames are not yet modeled. This is documented as not survivorship-safe; a permanent security master is required before backtesting claims. |
| D018 | 2026-10-24 | Ingestion retries only transient failures (429, 5xx, network), with exponential backoff | Non-retryable 4xx errors fail immediately. The token is read from `TIINGO_API_KEY` and excluded from URLs, manifests, and error text. |
| D019 | 2026-10-24 | Parquet (`pyarrow`) for normalized storage; JSON manifests for provenance | Efficient typed storage for price panels; raw payloads remain verbatim. Single-writer only; file locking is deferred until concurrent ingestion is needed. |
| D020 | 2026-10-24 | No automatic `.env` loading; credentials come from the process environment | Avoids an extra dependency and accidental secret loading. The user's environment security policy also blocks `.env*` files in this workspace. |
