# Modeling Conventions (Phase 3)

This document defines the feature, label, split, model, and artifact conventions
implemented in Phase 3. **These conventions are research infrastructure, not
evidence of predictive power.** The default dataset is synthetic random-walk
data: metrics produced from it test the pipeline, not the market.

The pipeline fits one chronological train/test split. It is not a backtest:
there is no walk-forward loop, portfolio, execution model, or transaction cost.
Those are Phase 4 work.

## Input and research universe

`stock-returns train` reads a price table (CSV or Parquet) with these required
columns:

| Column | Meaning |
|---|---|
| `security_id` | Stable identifier for the security |
| `date` | Exchange trading date of the daily bar |
| `close` | Raw close, used for dollar volume |
| `volume` | Daily share volume |
| `adj_close` | Split- and dividend-adjusted close; preferred when present |

`adj_close` is used for returns and labels whenever the column exists. If it is
absent, the pipeline falls back to `close` and records
`return_price_column: close` in the manifest. The fallback is intended for the
generated synthetic data, which has no corporate actions. For real market data,
ingest adjusted closes (Phase 2 already requires them) and check the manifest.

The universe is every `security_id` in the input with enough history to compute
features and a realized label. There is **no liquidity, market-cap, exchange, or
index-membership screen** yet. Ingestion identifiers are still provisional
`provider:symbol` values, so the dataset must not be described as
survivorship-bias-free.

## Trading calendar and availability

- The calendar is the sorted union of `date` values in the price panel. It is
  not an official exchange calendar; exchange holidays simply do not appear.
- A bar dated D is treated as available at **21:00 UTC on D**. This is at or
  after the 16:00 US regular-session close in both EST (21:00 UTC) and EDT
  (20:00 UTC).
- Every feature at D uses bars dated D or earlier.
- Missing bars are never forward-filled. A security is reindexed to the union
  calendar, so a gap makes the affected trailing windows missing until enough
  new observations accumulate.

## Labels

For horizon `h` in `[21, 42, 63]` trading days:

```
forward_return(D, h) = return_price(D + h) / return_price(D) - 1
benchmark_return(D, h) = mean over labeled securities of forward_return(D, h)
excess_return(D, h) = forward_return(D, h) - benchmark_return(D, h)
```

- `D + h` is the h-th following date on the union trading calendar.
- `return_price` is `adj_close` when available, otherwise `close`.
- The benchmark is the equal-weight mean of the same-horizon forward returns of
  all securities with a valid label at D. It is a research universe benchmark,
  not an investable index.
- A label is available at **21:00 UTC on its end date** (`D + h`).
- A security with a missing bar at D or D+h has no label at D. The benchmark at
  D excludes securities without a valid label.
- Consecutive labels overlap. The single chronological split purges every
  training row whose label matures after the test start. Walk-forward purging
  and embargoes are deferred to Phase 4.

Label timestamps and returns are forward-looking by construction. They are used
only as training targets and evaluation outcomes, never as model inputs.

## Features

All features are trailing and available at the close of D.

| Feature | Definition | Lookback |
|---|---|---|
| `momentum_5` | `return_price(D) / return_price(D-5) - 1` | 5 days |
| `momentum_21` | 21-trading-day return | 21 days |
| `momentum_63` | 63-trading-day return | 63 days |
| `volatility_21` | Sample standard deviation of daily returns over 21 days | 21 days |
| `log_dollar_volume_21` | `log1p` of mean daily `close * volume` over 21 days | 21 days |
| `market_relative_momentum_21` | `momentum_21` minus its cross-sectional mean on D | 21 days |

The cross-sectional mean uses only same-date data. Feature rows with any missing
value are dropped before training; nothing is imputed at feature-construction
time. For the ridge model, median imputation and standard scaling live inside the
training pipeline and are fitted on training rows only. `hist-gbm` is fitted on
the same complete-case rows.

## Models

All models predict `excess_return`. They are evaluated on identical test rows,
dates, and targets.

| Model ID | Description | Configuration |
|---|---|---|
| `momentum` | Rule-based baseline; prediction is `momentum_21` | none |
| `ridge` | Median imputation, standard scaling, ridge regression | `alpha = 1.0` |
| `hist-gbm` | scikit-learn `HistGradientBoostingRegressor` | 200 iterations, learning rate 0.05, 31 leaves, min 20 samples per leaf, L2 1.0, seeded |

`expected_volatility` is a diagnostic estimate: trailing 21-day daily return
volatility times `sqrt(horizon)`. It is not a fitted forecast. `prob_positive` is
a normal CDF of predicted excess return divided by that volatility. Neither is
calibrated; do not present `prob_positive` as a calibrated probability.

The momentum rule's predicted scale does not match the target's scale, so its
RMSE is not directly comparable to the fitted models. Rank IC is the primary
cross-sectional comparison.

## Chronological split and leakage controls

1. Dates are sorted. The first `train_fraction` (default 0.7) of labeled dates
   defines the training window; the rest is the test window.
2. Training rows are restricted to dates before the test start.
3. Any training row whose label matures after the test start is purged. This is
   required because 21/42/63-day labels overlap.
4. The test set contains only dates with realized labels, so the final `h` dates
   of the input have no predictions.
5. Preprocessing is fitted on training rows only.

Automated tests verify that future prices cannot change past features, that
forward prices do change labels, that splits are chronological, that overlap
purging occurs, and that changing test features does not change fitted
preprocessing statistics. A single split is still a weak evaluation design; it
does not establish out-of-sample skill.

## Metrics

| Metric | Meaning |
|---|---|
| `rmse` | Root mean squared error of predicted vs realized excess return |
| `mae` | Mean absolute error |
| `hit_rate` | Share of non-zero predictions whose sign matches the realized excess return |
| `mean_daily_rank_ic` | Mean Spearman correlation between predictions and realized excess returns, computed per date |
| `std_daily_rank_ic` | Standard deviation of the daily rank ICs |
| `n_rank_ic_dates` | Number of dates with at least three securities and non-constant values |

Rank IC dates with fewer than three securities are skipped. The synthetic data
has no market signal, so any non-zero metric is sampling noise or overfitting.

## Artifacts

Each run writes a new directory under `training.output_dir`:

```
data/models/<experiment_id>/
  predictions.parquet
  manifest.json
```

The experiment id is the UTC timestamp plus a short hash of the dataset content
hash and configuration hash. Existing experiment directories are never
overwritten.

### `predictions.parquet`

| Column | Meaning |
|---|---|
| `model_id` | `momentum`, `ridge`, or `hist-gbm` |
| `security_id` | Security identifier |
| `date` | Trading date of the forecast features |
| `as_of` | UTC timestamp of the bar's availability (21:00 on `date`) |
| `horizon_days` | 21, 42, or 63 |
| `predicted_excess_return` | Model output (expected excess return) |
| `expected_volatility` | Trailing volatility scaled to the horizon |
| `prob_positive` | Normal approximation; uncalibrated |
| `realized_forward_return` | Outcome used for evaluation |
| `realized_benchmark_return` | Equal-weight benchmark outcome |
| `realized_excess_return` | Outcome minus benchmark outcome |

Including realized outcomes makes the file an evaluation artifact. A future
prediction-only command must not include them.

`predicted_excess_return` maps to `Prediction.expected_return` in the Phase 1
contract; the semantic target (excess return versus the equal-weight benchmark)
is recorded in the manifest rather than inferred from the column name.
`expected_volatility` and `prob_positive` match the corresponding contract
fields, with the calibration caveat above.

### `manifest.json`

Records the schema and label versions, package version, config hash, seed,
horizons, split fraction, model hyperparameters, dataset path and SHA-256,
return-price column, feature definitions, label convention, per-horizon split
boundaries and purge counts, metrics, prediction file hash, and limitations.

Fitted model objects are not persisted. Reproducing predictions requires the
same input data, configuration, and code version.

## Commands

Synthetic pipeline smoke test:

```bash
uv run stock-returns --config configs/default.toml generate-synthetic
uv run stock-returns --config configs/default.toml train
```

Training on an ingested normalized dataset (requires Phase 2 ingestion):

```bash
uv run stock-returns --config configs/default.toml train \
  --prices data/normalized/prices.parquet \
  --out data/models
```

The command prints the experiment id, total train/test rows, and per-horizon
metrics for each model, then the predictions and manifest paths.

## Known limitations

- One chronological holdout is not a walk-forward backtest; repeated
  experimentation on it would overfit the holdout.
- No transaction costs, slippage, market impact, liquidity constraints, or
  execution timing. Labels are close-to-close, which is not necessarily
  executable after the close.
- No portfolio construction, risk model, sector neutralization, or position
  limits.
- Equal-weight universe benchmark only.
- No calibrated probabilities or uncertainty intervals.
- No model artifact persistence or model registry.
- Features are a small price/volume set; no fundamentals, news, or relationships.
- Synthetic data metrics are meaningless as market evidence.
- Real-data training inherits the input's survivorship, identifier, and
  adjustment limitations documented in [DATA_SOURCES.md](DATA_SOURCES.md).
