# Stock Returns Research Platform

A research platform for ranking US-listed stocks by expected risk-adjusted
returns over 1–3 month horizons. This is **research software, not investment
advice**, and it makes no claim that any model can beat the market.

## Status

**Phase 1 — foundation — complete.** The repository currently contains no
market-data ingestion, features, labels, models, or backtests. All data used so
far is synthetic and exists only to test the infrastructure.

See [docs/ROADMAP.md](docs/ROADMAP.md) for the phase plan and
[docs/DECISIONS.md](docs/DECISIONS.md) for the decision log.

## Requirements

- Python **3.11+** (Phase 1 was verified on 3.11.9; newer versions are not yet verified)
- [uv](https://docs.astral.sh/uv/) (recommended) or `pip`

No API keys, network access, or paid data subscriptions are needed for any
command or test in Phase 1.

## Setup

```bash
uv sync --extra dev
```

This creates `.venv`, resolves dependencies from `uv.lock`, and installs the
project in editable mode. Without uv:

```bash
python -m venv .venv
source .venv/bin/activate        # Windows: .venv\Scripts\activate
pip install -e ".[dev]"
```

## Commands

Run from the repository root. `--config` must precede the subcommand.

### 1. Health check

```bash
uv run stock-returns --config configs/default.toml health
```

Prints JSON describing the interpreter, pandas version, loaded config, and the
declared forecast horizons. A `"status": "ok"` result proves the package, config
loader, and CLI are working.

### 2. Generate synthetic data

```bash
uv run stock-returns --config configs/default.toml generate-synthetic
```

Writes `data/synthetic/securities.csv` and `data/synthetic/prices.csv`
(8 securities × 260 business days by default). Generation is deterministic for
a given seed: the same config always produces identical files. The data is
artificial and carries no economic information.

### 3. Validate data

```bash
uv run stock-returns --config configs/default.toml validate
```

Checks required columns, nulls, types, parseable dates, duplicate
`(security_id, date)` keys, OHLC consistency, positive prices and volumes, the
security master, and referential integrity. Valid input prints a one-line
summary and exits 0; invalid input prints every detected problem (up to the
configured cap) and exits 1.

## Tests and quality checks

```bash
uv run pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

The test suite is fully offline and deterministic. It requires no credentials.

## Continuous integration

`.github/workflows/ci.yml` runs on every push and pull request. On Ubuntu and
Windows with Python 3.11 it installs from `uv.lock`, then runs:

- `ruff check` and `ruff format --check`
- `mypy`
- `pytest`
- a CLI smoke test (`health` → `generate-synthetic` → `validate`)

## Layout

```
configs/default.toml          Documented, validated configuration
src/stock_returns/
  cli.py                      `health`, `generate-synthetic`, `validate`
  config.py                   Typed TOML loading; rejects unknown keys
  contracts.py                Security, Observation, Feature, Label,
                              Prediction, ExperimentRecord
  validation.py               Price/security schema and quality checks
  synthetic.py                Deterministic synthetic dataset
  seeding.py                  Explicit-seed RNG convention
  logging_utils.py            CLI logging setup
  errors.py                   User-facing exception hierarchy
tests/                        Offline unit, integration, and CLI tests
docs/ROADMAP.md               Phased plan and status
docs/DECISIONS.md             Architecture decision log
```

## Data and time conventions

- **Point-in-time fields are separate.** `observed_at`/`as_of` describes the
  period; `available_at` is when the information was knowable. Contracts reject
  `available_at < observed_at`.
- **All datetimes are timezone-aware.** UTC is the project convention.
- **Security identity is a stable `security_id`, never a ticker.** Tickers can
  change; historical ticker history is not implemented yet.
- **Daily bars are labeled by exchange trading date.** The synthetic calendar is
  weekdays only and ignores US market holidays.
- **Forecast horizons** are 21, 42, and 63 trading days, approximating 1, 2, and
  3 calendar months.
- **Randomness** uses an explicitly seeded `numpy.random.Generator`; pipeline
  code must not use global RNG state.

## Known limitations

- No ingestion, benchmark data, empirical features or labels, model, ranking, or
  backtest exists yet; `Label` and `Prediction` are contracts only.
- Only synthetic data is available. No conclusion about predictive performance
  can be drawn from it.
- Storage is CSV; Parquet/DuckDB and experiment tracking arrive in later phases.
- Delisted securities, historical index membership, corporate actions, and
  point-in-time fundamentals are not handled yet.
- The validation layer covers the documented columns; it is not yet a full
  provider-specific reconciliation suite.
- Local verification has been on Windows with Python 3.11; CI covers Ubuntu and
  Windows but first runs when the repository is pushed to GitHub.
