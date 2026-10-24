# Roadmap

Only the current approved phase is implemented. Each phase ends with automated
verification and an explicit approval request.

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Foundation: structure, config, contracts, synthetic data, validation, CLI, tests | **Complete** — 2026-10-24 |
| 2 | Historical data ingestion and storage; provider adapters; provenance | **Complete** — 2026-10-24 (live provider verification pending credentials) |
| 3 | Point-in-time features; 21/42/63-day labels; baseline and gradient-boosted models | **Complete** — 2026-10-24 |
| 4 | Chronological walk-forward backtesting and benchmark evaluation | Not started |
| 5 | Point-in-time fundamentals (e.g. SEC EDGAR/XBRL), with coverage caveats | Not started |
| 6 | Financial text and event extraction with publication timestamps | Not started |
| 7 | Advanced models and sourced company-relationship graph; ablations | Not started |
| 8 | Ranking, portfolio constraints, and risk controls (research simulation only) | Not started |
| 9 | Reproducibility, reporting, and operational hardening | Not started |

## Phase 1 acceptance criteria (met)

- [x] Project installs with documented commands.
- [x] CLI health, synthetic-data, and validation commands run.
- [x] Synthetic data is deterministic and validates offline.
- [x] Invalid data is rejected with understandable, multiple-issue errors.
- [x] Essential tests run without credentials or network.
- [x] CI runs lint, types, tests, and a CLI smoke test on Ubuntu and Windows.

## Phase 2 acceptance criteria

- [x] Ingestion works against a deterministic provider (CSV) and a mocked HTTP
  provider (Tiingo).
- [x] Normalization produces a validated, canonical dataset.
- [x] Re-running ingestion does not duplicate rows; raw snapshots and per-row
  provenance are retained.
- [x] Source failures (including invalid payloads and HTTP errors) are handled,
  logged, and never partially replace the normalized dataset.
- [x] Provider coverage, licensing, and missing-data limitations are documented
  in [DATA_SOURCES.md](DATA_SOURCES.md).
- [ ] Live Tiingo behavior and real historical/delisted coverage verified with
  credentials — **blocked: no API token available**. The gated integration test
  is present and skipped.

Deliberately excluded from Phase 2: features, labels, models, backtests,
fundamentals, news, and any survivorship-free claim.

## Phase 2 prerequisites (met)

- Approval to proceed.
- Documented provider evaluation covering history, delisted coverage,
  adjustments, identifiers, licensing, rate limits, and cost.
- Credentials are not required for the offline suite; `TIINGO_API_KEY` enables
  optional live verification.

## Phase 3 acceptance criteria

- [x] Point-in-time trailing features use only bars dated on or before the
  forecast date; missing bars are not filled.
- [x] 21/42/63-trading-day forward returns, equal-weight benchmark returns, and
  excess-return labels are defined with explicit availability timestamps.
- [x] Overlapping labels are handled with a chronological split that purges
  training rows whose labels mature after the test start.
- [x] A momentum rule, ridge regression, and histogram gradient boosting run on
  identical chronological splits.
- [x] Preprocessing is fitted inside the training pipeline on training rows only.
- [x] Predictions use a documented schema and are aligned to the correct dates.
- [x] Dataset hash, config hash, feature definitions, label convention, split
  boundaries, model hyperparameters, and metrics are persisted in a manifest.
- [x] The deterministic `train` command runs on synthetic data; tests cover
  feature math, label alignment, temporal integrity, purge behavior,
  preprocessing isolation, output schema, and reproducibility.
- [x] Conventions and limitations are documented in
  [MODELING.md](MODELING.md).

The synthetic metrics prove the pipeline runs, not that the models predict
returns. A single holdout is not a backtest.

## Phase 3 prerequisites (met)

- Approval to proceed.
- A label convention (close-to-close adjusted return, equal-weight universe
  benchmark, 21:00 UTC availability) and a trading-calendar convention.
- A decision to keep synthetic data for pipeline verification while real data
  ingestion remains available through the Phase 2 provider adapters.

## Phase 4 prerequisites

- Approval to proceed.
- A walked-forward evaluation design: retraining cadence, training window,
  purge/embargo rules for overlapping labels, and the evaluation calendar.
- Explicit execution assumptions (decision time, fill price, costs, slippage,
  turnover) and benchmark definitions.
- A policy for reporting results by period and market regime, and for avoiding
  repeated selection on the final test period.

