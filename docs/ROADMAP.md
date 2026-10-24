# Roadmap

Only the current approved phase is implemented. Each phase ends with automated
verification and an explicit approval request.

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Foundation: structure, config, contracts, synthetic data, validation, CLI, tests | **Complete** — 2026-10-24 |
| 2 | Historical data ingestion and storage; provider adapters; provenance | **Complete** — 2026-10-24 (live provider verification pending credentials) |
| 3 | Point-in-time features; 21/42/63-day labels; baseline and gradient-boosted models | Not started |
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

## Phase 3 prerequisites

- Approval to proceed.
- A decision on the research universe and whether to ingest real data with a
  chosen licensed provider or continue with synthetic data for pipeline tests.
- Definition of the label return convention (adjusted close total return vs
  benchmark excess return) and the trading calendar.

