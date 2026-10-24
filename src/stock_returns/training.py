"""Dataset assembly, chronological holdout, baseline models, and artifacts.

This module fits a single chronological train/test split. It is deliberately
not a backtest: there is no walk-forward loop, portfolio construction, execution
model, or transaction cost. Those belong to Phase 4.

Models are compared on the same rows and target:

* ``momentum`` — rule-based ranker using ``momentum_21`` directly.
* ``ridge`` — linear regression with median imputation and standard scaling,
  both fitted on training rows only.
* ``hist-gbm`` — scikit-learn histogram gradient boosting.

The prediction target is the forward excess return versus the equal-weight
research universe. ``prob_positive`` is a normal approximation from the
predicted excess return and trailing realized volatility; it is not calibrated.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from statistics import NormalDist
from typing import Any

import numpy as np
import pandas as pd
import pyarrow as pa
from sklearn.ensemble import HistGradientBoostingRegressor
from sklearn.impute import SimpleImputer
from sklearn.linear_model import Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

from . import __version__
from .config import Config
from .errors import TrainingError
from .features import (
    FEATURE_COLUMNS,
    FEATURE_SET_VERSION,
    FEATURE_SPECS,
    PricePanel,
    compute_features,
    prepare_panel,
)
from .labels import compute_labels, market_close_timestamp

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 1
LABEL_VERSION = 1
MODEL_IDS = ("momentum", "ridge", "hist-gbm")
RIDGE_ALPHA = 1.0
GBM_PARAMS: dict[str, object] = {
    "max_iter": 200,
    "learning_rate": 0.05,
    "max_leaf_nodes": 31,
    "min_samples_leaf": 20,
    "l2_regularization": 1.0,
}

PREDICTION_COLUMNS = (
    "model_id",
    "security_id",
    "date",
    "as_of",
    "horizon_days",
    "predicted_excess_return",
    "expected_volatility",
    "prob_positive",
    "realized_forward_return",
    "realized_benchmark_return",
    "realized_excess_return",
)

MetricValue = float | int | None
Metrics = dict[str, MetricValue]
_NORMAL = NormalDist()


@dataclass(frozen=True)
class TrainingResult:
    """Paths and summary metrics from one completed training run."""

    experiment_id: str
    run_dir: Path
    predictions_path: Path
    manifest_path: Path
    splits: dict[str, dict[str, object]]
    metrics: dict[str, dict[str, Metrics]]
    train_rows: int
    test_rows: int


@dataclass(frozen=True)
class ChronologicalSplit:
    train: pd.DataFrame
    test: pd.DataFrame
    test_start_date: pd.Timestamp
    test_start: pd.Timestamp
    purged_train_rows: int


@dataclass(frozen=True)
class ModelFit:
    model_id: str
    predictions: np.ndarray
    estimator: object | None


def read_price_table(path: str | Path) -> pd.DataFrame:
    """Read a CSV or Parquet price table for training."""
    source = Path(path)
    if not source.is_file():
        raise TrainingError(
            f"price file not found: {source}; run `stock-returns generate-synthetic` "
            "or pass --prices"
        )
    suffix = source.suffix.lower()
    try:
        if suffix == ".parquet":
            return pd.read_parquet(source)
        if suffix == ".csv":
            return pd.read_csv(source)
    except (
        OSError,
        UnicodeDecodeError,
        ValueError,
        pd.errors.ParserError,
        pd.errors.EmptyDataError,
        pa.ArrowException,
    ) as exc:
        raise TrainingError(f"cannot read price file {source}: {exc}") from exc
    raise TrainingError(f"unsupported price file type {source.suffix!r}; use .csv or .parquet")


def build_training_dataset(panel: PricePanel, horizons: Sequence[int]) -> pd.DataFrame:
    """Join features and labels and drop rows without a complete observation."""
    if panel.frame["security_id"].nunique() < 2:
        raise TrainingError("at least two securities are required for cross-sectional training")

    features = compute_features(panel)
    labels = compute_labels(panel, horizons)
    dataset = labels.merge(
        features, on=["security_id", "date"], how="inner", validate="many_to_one"
    )
    complete = dataset.dropna(subset=list(FEATURE_COLUMNS))
    dropped = len(dataset) - len(complete)
    if complete.empty:
        raise TrainingError(
            "no rows have both complete features and realized labels; the longest feature "
            "lookback is 63 trading days and labels need a further 21-63 future days. "
            "Supply a longer price history."
        )

    complete = complete.sort_values(["horizon_days", "date", "security_id"]).reset_index(drop=True)
    if bool((complete["label_available_at"] <= complete["as_of"]).any()):
        raise TrainingError("label availability must be after as_of")
    logger.info(
        "training dataset: %d row(s), %d dropped for incomplete features, %d securities",
        len(complete),
        dropped,
        complete["security_id"].nunique(),
    )
    return complete


def chronological_split(dataset: pd.DataFrame, train_fraction: float) -> ChronologicalSplit:
    """Split by date and purge training labels that mature after the test start.

    Overlapping forward-return labels mean a training row near the split can
    contain information from the test period. Such rows are removed.
    """
    if not 0.0 < train_fraction < 1.0:
        raise TrainingError(f"train_fraction must be between 0 and 1, got {train_fraction}")
    if dataset.empty:
        raise TrainingError("cannot split an empty dataset")

    dates = pd.DatetimeIndex(sorted(dataset["date"].unique()))
    if len(dates) < 2:
        raise TrainingError("at least two labeled trading dates are required for a split")

    split_index = min(max(int(len(dates) * train_fraction), 1), len(dates) - 1)
    test_start_date = pd.Timestamp(dates[split_index])
    test_start = market_close_timestamp(test_start_date)

    candidates = dataset.loc[dataset["date"] < test_start_date]
    test = dataset.loc[dataset["date"] >= test_start_date].reset_index(drop=True)
    train = candidates.loc[candidates["label_available_at"] <= test_start].reset_index(drop=True)
    purged = len(candidates) - len(train)
    if train.empty:
        raise TrainingError(
            f"no training rows remain after purging labels that mature after "
            f"{test_start_date.date()}; supply a longer history or a shorter horizon"
        )
    if test.empty:
        raise TrainingError("the chronological test split is empty")
    return ChronologicalSplit(
        train=train,
        test=test,
        test_start_date=test_start_date,
        test_start=test_start,
        purged_train_rows=purged,
    )


def fit_and_predict(
    model_id: str,
    train: pd.DataFrame,
    test: pd.DataFrame,
    *,
    seed: int,
) -> ModelFit:
    """Fit one model on ``train`` and predict ``test``.

    Preprocessing lives inside the fitted pipeline, so imputation and scaling
    statistics are learned from training rows only.
    """
    if model_id == "momentum":
        predictions = test["momentum_21"].to_numpy(dtype=float)
        return ModelFit(model_id=model_id, predictions=predictions, estimator=None)

    x_train = train.loc[:, list(FEATURE_COLUMNS)].to_numpy(dtype=float)
    y_train = train["excess_return"].to_numpy(dtype=float)
    x_test = test.loc[:, list(FEATURE_COLUMNS)].to_numpy(dtype=float)

    estimator: Any
    if model_id == "ridge":
        estimator = Pipeline(
            [
                ("impute", SimpleImputer(strategy="median")),
                ("scale", StandardScaler()),
                ("model", Ridge(alpha=RIDGE_ALPHA)),
            ]
        )
    elif model_id == "hist-gbm":
        estimator = HistGradientBoostingRegressor(**{**GBM_PARAMS, "random_state": seed})
    else:
        raise TrainingError(f"unknown model_id: {model_id!r}")

    estimator.fit(x_train, y_train)
    predictions = np.asarray(estimator.predict(x_test), dtype=float)
    if not bool(np.isfinite(predictions).all()):
        raise TrainingError(f"{model_id} produced non-finite predictions")
    return ModelFit(model_id=model_id, predictions=predictions, estimator=estimator)


def _rank_ic(group: pd.DataFrame) -> float:
    if len(group) < 3:
        return float("nan")
    if (
        group["predicted_excess_return"].nunique() < 2
        or group["realized_excess_return"].nunique() < 2
    ):
        return float("nan")
    return float(
        group["predicted_excess_return"].corr(group["realized_excess_return"], method="spearman")
    )


def _finite_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return value if math.isfinite(value) else None


def evaluate_predictions(frame: pd.DataFrame) -> Metrics:
    """Compute holdout error, direction, and daily cross-sectional rank IC."""
    predicted = frame["predicted_excess_return"].to_numpy(dtype=float)
    realized = frame["realized_excess_return"].to_numpy(dtype=float)
    error = predicted - realized
    nonzero = predicted != 0.0
    hit_rate = (
        float(np.mean(np.sign(predicted[nonzero]) == np.sign(realized[nonzero])))
        if bool(nonzero.any())
        else None
    )

    daily_ic = (
        frame.groupby("date", sort=True)[["predicted_excess_return", "realized_excess_return"]]
        .apply(_rank_ic)
        .dropna()
        .to_numpy(dtype=float)
    )
    return {
        "n_predictions": int(len(frame)),
        "n_dates": int(frame["date"].nunique()),
        "rmse": _finite_or_none(float(np.sqrt(np.mean(error**2)))),
        "mae": _finite_or_none(float(np.mean(np.abs(error)))),
        "hit_rate": _finite_or_none(hit_rate),
        "mean_daily_rank_ic": _finite_or_none(float(np.mean(daily_ic))) if len(daily_ic) else None,
        "std_daily_rank_ic": _finite_or_none(float(np.std(daily_ic, ddof=1)))
        if len(daily_ic) > 1
        else None,
        "n_rank_ic_dates": int(len(daily_ic)),
    }


def _prob_positive(expected_return: float, volatility: float) -> float:
    if volatility <= 0.0:
        if expected_return > 0.0:
            return 1.0
        if expected_return < 0.0:
            return 0.0
        return 0.5
    return float(_NORMAL.cdf(expected_return / volatility))


def _prediction_frame(test: pd.DataFrame, horizon: int, fit: ModelFit) -> pd.DataFrame:
    expected_volatility = test["volatility_21"].to_numpy(dtype=float) * math.sqrt(horizon)
    probabilities = [
        _prob_positive(float(prediction), float(volatility))
        for prediction, volatility in zip(fit.predictions, expected_volatility, strict=True)
    ]
    return pd.DataFrame(
        {
            "model_id": fit.model_id,
            "security_id": test["security_id"].to_numpy(),
            "date": test["date"].to_numpy(),
            "as_of": test["as_of"].to_numpy(),
            "horizon_days": horizon,
            "predicted_excess_return": fit.predictions,
            "expected_volatility": expected_volatility,
            "prob_positive": probabilities,
            "realized_forward_return": test["forward_return"].to_numpy(dtype=float),
            "realized_benchmark_return": test["benchmark_return"].to_numpy(dtype=float),
            "realized_excess_return": test["excess_return"].to_numpy(dtype=float),
        }
    ).loc[:, list(PREDICTION_COLUMNS)]


def compute_dataset_id(panel: PricePanel) -> str:
    """Content hash of the canonical price panel used by an experiment."""
    canonical = panel.frame.loc[
        :, ["security_id", "date", "return_price", "close", "volume"]
    ].copy()
    canonical["date"] = canonical["date"].dt.strftime("%Y-%m-%d")
    payload = f"return_price_column={panel.return_price_column}\n" + canonical.to_csv(
        index=False, float_format="%.17g"
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def model_metadata(seed: int) -> dict[str, dict[str, object]]:
    """Model descriptions and hyperparameters recorded in the manifest."""
    return {
        "momentum": {
            "family": "rule",
            "description": "predicted excess return equals momentum_21",
            "params": {"signal": "momentum_21"},
        },
        "ridge": {
            "family": "linear",
            "description": "median imputation, standard scaling, ridge regression",
            "params": {"alpha": RIDGE_ALPHA},
        },
        "hist-gbm": {
            "family": "gradient_boosting",
            "description": "scikit-learn histogram gradient boosting",
            "params": {**GBM_PARAMS, "random_state": seed},
        },
    }


def _sha256_json(payload: object) -> str:
    encoded = json.dumps(payload, sort_keys=True, allow_nan=False).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _make_experiment_id(created_at: datetime, dataset_id: str, config_hash: str) -> str:
    digest = hashlib.sha256(f"{dataset_id}:{config_hash}".encode()).hexdigest()[:8]
    return f"{created_at:%Y%m%dT%H%M%SZ}-{digest}"


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(
            json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
            encoding="utf-8",
        )
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def run_training(
    prices: pd.DataFrame,
    *,
    config: Config,
    output_dir: str | Path | None = None,
    train_fraction: float | None = None,
    dataset_path: str | Path | None = None,
    now: Callable[[], datetime] | None = None,
) -> TrainingResult:
    """Build features and labels, fit models once, and persist predictions.

    The split is chronological. Training rows whose labels mature after the test
    start are purged. The manifest records the dataset hash, feature definitions,
    label convention, split boundaries, model hyperparameters, and metrics.
    """
    fraction = config.training.train_fraction if train_fraction is None else train_fraction
    if not 0.0 < fraction < 1.0:
        raise TrainingError(f"train_fraction must be between 0 and 1, got {fraction}")

    created_at = (now or (lambda: datetime.now(UTC)))()
    if created_at.tzinfo is None or created_at.utcoffset() is None:
        raise TrainingError("created_at must be timezone-aware")
    created_at = created_at.astimezone(UTC)

    panel = prepare_panel(prices)
    dataset = build_training_dataset(panel, config.horizons)
    dataset_id = compute_dataset_id(panel)
    models = model_metadata(config.seed)
    configuration = {
        "feature_set_version": FEATURE_SET_VERSION,
        "label_version": LABEL_VERSION,
        "seed": config.seed,
        "horizons": list(config.horizons),
        "train_fraction": fraction,
        "models": models,
    }
    config_hash = _sha256_json(configuration)
    experiment_id = _make_experiment_id(created_at, dataset_id, config_hash)
    run_dir = Path(output_dir if output_dir is not None else config.training.output_dir) / (
        experiment_id
    )
    if run_dir.exists():
        raise TrainingError(f"experiment output directory already exists: {run_dir}")

    prediction_frames: list[pd.DataFrame] = []
    metrics: dict[str, dict[str, Metrics]] = {}
    splits: dict[str, dict[str, object]] = {}
    train_rows = 0
    test_rows = 0

    for horizon in config.horizons:
        horizon_data = dataset.loc[dataset["horizon_days"] == horizon]
        if horizon_data.empty:
            raise TrainingError(f"horizon {horizon}: no labeled rows are available")
        split = chronological_split(horizon_data, fraction)
        train_rows += len(split.train)
        test_rows += len(split.test)
        splits[str(horizon)] = {
            "train_rows": len(split.train),
            "test_rows": len(split.test),
            "purged_train_rows": split.purged_train_rows,
            "train_start_date": split.train["date"].min().date().isoformat(),
            "train_end_date": split.train["date"].max().date().isoformat(),
            "test_start_date": split.test_start_date.date().isoformat(),
            "test_end_date": split.test["date"].max().date().isoformat(),
            "train_label_cutoff": split.test_start.isoformat(),
        }

        horizon_metrics: dict[str, Metrics] = {}
        for model_id in MODEL_IDS:
            fit = fit_and_predict(model_id, split.train, split.test, seed=config.seed)
            frame = _prediction_frame(split.test, horizon, fit)
            horizon_metrics[model_id] = evaluate_predictions(frame)
            prediction_frames.append(frame)
        metrics[str(horizon)] = horizon_metrics

    predictions = pd.concat(prediction_frames, ignore_index=True)
    predictions = predictions.sort_values(
        ["horizon_days", "date", "model_id", "security_id"]
    ).reset_index(drop=True)
    try:
        run_dir.mkdir(parents=True, exist_ok=False)
    except FileExistsError as exc:
        raise TrainingError(f"experiment output directory already exists: {run_dir}") from exc
    predictions_path = run_dir / "predictions.parquet"
    predictions.to_parquet(predictions_path, index=False)

    manifest: dict[str, object] = {
        "schema_version": SCHEMA_VERSION,
        "experiment_id": experiment_id,
        "created_at": created_at.isoformat(),
        "package_version": __version__,
        "config_hash": config_hash,
        "configuration": configuration,
        "dataset": {
            "path": Path(dataset_path).as_posix() if dataset_path is not None else "<in-memory>",
            "sha256": dataset_id,
            "securities": int(panel.frame["security_id"].nunique()),
            "price_rows": int(len(panel.frame)),
            "first_date": panel.frame["date"].min().date().isoformat(),
            "last_date": panel.frame["date"].max().date().isoformat(),
            "return_price_column": panel.return_price_column,
        },
        "features": {
            "version": FEATURE_SET_VERSION,
            "columns": [
                {
                    "name": spec.name,
                    "definition": spec.definition,
                    "lookback_days": spec.lookback_days,
                }
                for spec in FEATURE_SPECS
            ],
        },
        "labels": {
            "version": LABEL_VERSION,
            "horizons": list(config.horizons),
            "target": "excess_return",
            "return_convention": "close-to-close total return using the return price "
            "column; an adjusted close already includes splits and distributions",
            "benchmark": "equal-weight mean of the same-horizon forward returns of all "
            "labeled securities on the same date",
            "availability_time_utc": "21:00 on the bar's exchange trading date",
            "missing_bars": "not forward-filled; a missing endpoint makes the label missing",
            "overlap": "labels overlap across adjacent dates; training rows whose labels "
            "mature after the test start are purged",
            "prob_positive": "normal approximation from predicted excess return and "
            "trailing realized volatility; not calibrated",
        },
        "splits": splits,
        "metrics": metrics,
        "predictions": {
            "file": predictions_path.name,
            "sha256": _sha256_file(predictions_path),
            "rows": int(len(predictions)),
        },
        "limitations": [
            "single chronological holdout, not a walk-forward backtest",
            "no transaction costs, slippage, market impact, or execution model",
            "equal-weight universe benchmark, not an investable index",
            "no liquidity or investability screen",
            "synthetic data verifies mechanics only and carries no market signal",
            "fitted model objects are not persisted",
            "survivorship and adjustment caveats are inherited from the input dataset",
        ],
    }
    manifest_path = run_dir / "manifest.json"
    _write_json_atomic(manifest_path, manifest)

    logger.info(
        "experiment %s wrote %d predictions to %s", experiment_id, len(predictions), run_dir
    )
    return TrainingResult(
        experiment_id=experiment_id,
        run_dir=run_dir,
        predictions_path=predictions_path,
        manifest_path=manifest_path,
        splits=splits,
        metrics=metrics,
        train_rows=train_rows,
        test_rows=test_rows,
    )
