# Roadmap

Only the current approved phase is implemented. Each phase ends with automated
verification and an explicit approval request.

| Phase | Scope | Status |
|-------|-------|--------|
| 1 | Foundation: structure, config, contracts, synthetic data, validation, CLI, tests | **Complete** — 2026-10-24 |
| 2 | Historical data ingestion and storage; provider adapters; provenance | Not started |
| 3 | Point-in-time features; 21/42/63-day labels; baseline and gradient-boosted models | Not started |
| 4 | Chronological walk-forward backtesting and benchmark evaluation | Not started |
| 5 | Point-in-time fundamentals (e.g. SEC EDGAR/XBRL), with coverage caveats | Not started |
| 6 | Financial text and event extraction with publication timestamps | Not started |
| 7 | Advanced models and sourced company-relationship graph; ablations | Not started |
| 8 | Ranking, portfolio constraints, and risk controls (research simulation only) | Not started |
| 9 | Reproducibility, reporting, and operational hardening | Not started |

## Phase 1 acceptance criteria

- [x] Project installs with documented commands (`uv sync --extra dev`).
- [x] CLI runs `health`, `generate-synthetic`, and `validate`.
- [x] Synthetic dataset validates offline and deterministically.
- [x] Invalid data is rejected with understandable, multiple-issue errors.
- [x] Essential tests execute; results are reported in the phase report.
- [x] No paid API key or network access required.
- [x] Clear next step documented (Phase 2).

Deliberately excluded from Phase 1: any model, feature, label computation,
backtest, external data source, or web interface.

## Phase 2 prerequisites

- Approval to proceed.
- A decision on candidate providers after documenting historical depth,
  point-in-time availability, delisted-security coverage, corporate actions,
  licensing, rate limits, and cost.
- Credentials, if a selected provider requires them, supplied through
  environment variables — never committed to the repository.
