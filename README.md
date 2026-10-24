# Stock Returns Research Platform

A research platform for ranking US-listed stocks by expected risk-adjusted
returns over 1–3 month horizons. This is **research software, not investment
advice**, and it makes no claim that any model can beat the market.

## Status

**Phase 3 — features, labels, and the first predictive baselines — complete.**
The repository can ingest daily prices, build point-in-time features and
21/42/63-trading-day forward-return labels, fit a momentum baseline, ridge
regression, and histogram gradient boosting on a chronological holdout, and
persist predictions with a reproducibility manifest.

This is still **research software, not a backtest**: there is no walk-forward
loop, portfolio, execution model, or transaction cost yet. Bundled test data is
synthetic and carries no market signal. Metrics computed from it verify the
pipeline only.

See [docs/MODELING.md](docs/MODELING.md) for feature, label, split, metric, and
artifact conventions; [docs/ROADMAP.md](docs/ROADMAP.md) for the phase plan; and
[docs/DECISIONS.md](docs/DECISIONS.md) for the decision log.

## Requirements

- Python **3.11+** (verified on 3.11.9; newer versions are not yet verified)
- [uv](https://docs.astral.sh/uv/) (recommended) or `pip`

No API key or paid subscription is needed for the default test suite. The
optional live Tiingo integration test requires `TIINGO_API_KEY`.

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

### 4. Ingest market data

Local CSV export (fully offline):

```bash
uv run stock-returns --config configs/default.toml ingest \
  --provider csv --input path/to/csv_export \
  --start 2020-01-01 --end 2023-12-31
```

The input directory needs:

- `prices.csv` — `symbol,date,open,high,low,close,volume,adj_close`, plus optional
  `dividend_cash,split_factor`.
- `securities.csv` — `symbol,name,exchange`, plus optional
  `security_id,first_data_date,last_data_date`.

If coverage dates are omitted they are derived from the ingested window. Every
requested/listed symbol must have at least one bar in the date range; ingestion
fails rather than silently dropping a symbol.

Tiingo (requires network and a token; never commit the token):

```bash
# PowerShell: $env:TIINGO_API_KEY = "your-token"
export TIINGO_API_KEY="your-token"
uv run stock-returns --config configs/default.toml ingest \
  --provider tiingo --symbols AAPL,MSFT \
  --start 2020-01-01 --end 2023-12-31
```

The token is read only from the environment. There is no automatic `.env`
loading. A successful run prints the run id, rows received/new/total, raw
snapshot path, and normalized directory. Re-running the same request adds no
duplicate rows and preserves both raw snapshots.

### 5. Check coverage

```bash
uv run stock-returns --config configs/default.toml coverage
```

Reports per-security row counts, first/last dates, and missing weekdays.
Exchange holidays count as missing weekdays; this is a gap detector, not a
trading-calendar audit.

### 6. Build features/labels and train baseline models

On the generated synthetic data (fully offline):

```bash
uv run stock-returns --config configs/default.toml generate-synthetic
uv run stock-returns --config configs/default.toml train
```

On an ingested normalized dataset:

```bash
uv run stock-returns --config configs/default.toml train \
  --prices data/normalized/prices.parquet \
  --out data/models
```

The command builds trailing price/volume features, 21/42/63-trading-day forward
excess-return labels, a momentum baseline, ridge regression, and histogram
gradient boosting. It uses a single chronological split (default 70% train),
purges training labels that mature after the test start, fits preprocessing on
training rows only, and writes:

```
data/models/<experiment_id>/predictions.parquet   predictions and realized outcomes
data/models/<experiment_id>/manifest.json         dataset hash, config, split, metrics
```

The printed rank IC, RMSE, and hit rate are holdout diagnostics. Synthetic
results are noise and must not be interpreted as predictive skill. Full
conventions and limitations are in [docs/MODELING.md](docs/MODELING.md).

`--prices` accepts CSV or Parquet. `adj_close` is used automatically when
present; otherwise the training run falls back to `close`, records that choice
in the manifest, and should only be used for synthetic or corporate-action-free
data.

### Storage and provenance

```
data/raw/<provider>/<run_id>/manifest.json     request, timestamps, SHA-256 hashes
data/raw/<provider>/<run_id>/payloads/*        verbatim provider responses
data/normalized/prices.parquet                 latest revision per (security_id, date)
data/normalized/securities.parquet             provisional security master
data/normalized/manifest.json                  last run and file hashes
```

Every normalized row carries `provider`, `source_run_id`, and `ingested_at`.
Raw responses are retained per run. The normalized files hold the latest revision
and are replaced atomically only after the complete merged dataset validates.

## Tests and quality checks

```bash
uv run python -m pytest
uv run ruff check .
uv run ruff format --check .
uv run mypy
```

`python -m pytest` is used instead of the `pytest` launcher so Windows does not
have to approve the generated `.venv\Scripts\pytest.exe` shim.

The default suite is offline and deterministic. The live provider test is
marked `integration` and skipped unless `TIINGO_API_KEY` is set:

```bash
uv run python -m pytest                 # offline; live test skipped
uv run python -m pytest -m integration  # real network call; requires the key
```

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
  cli.py                      health, synthetic, validate, ingest, coverage, train
  config.py                   Typed TOML loading; rejects unknown keys
  contracts.py                Security, Observation, Feature, Label,
                              Prediction, ExperimentRecord
  features.py                 Point-in-time trailing price/volume features
  labels.py                   Forward returns, equal-weight benchmark, availability
  training.py                 Dataset assembly, purge, models, metrics, artifacts
  validation.py               Price/security schema and quality checks
  synthetic.py                Deterministic synthetic dataset
  seeding.py                  Explicit-seed RNG convention
  logging_utils.py            CLI logging setup
  errors.py                   User-facing exception hierarchy
  ingestion/
    base.py                   Provider protocol and batch types
    csv_provider.py           Licensed local CSV adapter
    tiingo.py                 Tiingo EOD adapter (mocked-tested; live test gated)
    normalize.py              Canonical schema and validation
    storage.py                Raw snapshots, atomic Parquet, provenance
    pipeline.py               Fetch → snapshot → merge → validate → commit
    coverage.py               Coverage and gap summary
tests/                        Offline unit/integration/CLI tests; live test gated
docs/MODELING.md              Feature, label, split, model, and artifact conventions
docs/DATA_SOURCES.md          Provider evaluation, coverage, licensing caveats
docs/ROADMAP.md               Phased plan and status
docs/DECISIONS.md             Architecture decision log
```

## Data and time conventions

- **Point-in-time fields are separate.** `observed_at`/`as_of` describes the
  period; `available_at` is when the information was knowable. Contracts reject
  `available_at < observed_at`.
- **All datetimes are timezone-aware.** UTC is the project convention.
- **Security identity is a stable `security_id`, never a ticker.** Ingested rows
  use provisional `provider:symbol` ids (or a vendor-supplied `security_id` from
  CSV). Ticker changes and reuse are not yet resolved, so do not treat these ids
  as a survivorship-safe security master.
- **Prices:** normalized `open/high/low/close/volume` are as reported by the
  provider (unadjusted); `adj_close` is split- and dividend-adjusted and is
  required. Total-return labels use `adj_close`. Training falls back to `close`
  only when `adj_close` is absent (the synthetic dataset) and records that in
  the run manifest.
- **Availability:** a daily bar dated D is treated as available at 21:00 UTC on
  D, after the US regular session closes in both EST and EDT. All features at D
  use bars dated D or earlier. No bar is forward-filled.
- **Labels:** forward return from close D to close D+h (h = 21/42/63 trading
  days). The benchmark is the equal-weight mean of the same-horizon forward
  returns of all labeled securities; the model target is the difference. Labels
  mature at 21:00 UTC on D+h, overlap across dates, and training rows whose
  labels mature after the test start are purged.
- **Synthetic calendar** is weekdays only and ignores US market holidays.
- **Forecast horizons** are 21, 42, and 63 trading days, approximating 1, 2, and
  3 calendar months.
- **Randomness** uses an explicitly seeded `numpy.random.Generator`; pipeline
  code must not use global RNG state. Model fits receive the configured seed.

## Known limitations

- The training pipeline uses one chronological holdout, not a walk-forward
  backtest. It has no transaction costs, slippage, execution model, portfolio
  construction, or risk controls. It does not establish out-of-sample skill.
- Predictions are trained on excess returns versus an equal-weight universe
  benchmark, not an investable index. `prob_positive` and
  `expected_volatility` are uncalibrated approximations.
- Only price/volume features exist; there are no fundamentals, news, or company
  relationships. No liquidity or index-membership screen is applied.
- Fitted model objects are not persisted. Reproducibility relies on the input
  dataset hash, configuration hash, and package version in the manifest.
- No live ingestion has been verified against a provider in this repository:
  the Tiingo adapter is tested with mocked responses, and the live test is
  skipped without credentials.
- Delisted-security coverage, historical ticker mapping, index membership, and
  point-in-time fundamentals are not solved. The dataset must not be described
  as survivorship-bias-free.
- Provider adjustments are revised over time. Raw snapshots are retained, but
  no bitemporal/vintage reconstruction of historical revisions is implemented.
- Normalized storage is a single-writer Parquet snapshot; there is no database,
  file lock, or concurrent-writer protection.
- `coverage` treats exchange holidays as missing weekdays.
- Data licensing is the user's responsibility; evaluated providers are for
  internal research use and restrict redistribution.
- Local verification has been on Windows with Python 3.11; CI covers Ubuntu and
  Windows but has not yet run after the Phase 3 changes.
