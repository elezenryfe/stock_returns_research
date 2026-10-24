import json
from datetime import UTC, datetime
from pathlib import Path
from typing import cast

import numpy as np
import pandas as pd
import pytest
from sklearn.pipeline import Pipeline

from stock_returns.config import Config, SyntheticSettings
from stock_returns.errors import TrainingError
from stock_returns.features import FEATURE_COLUMNS, prepare_panel
from stock_returns.synthetic import generate_synthetic_dataset
from stock_returns.training import (
    MODEL_IDS,
    PREDICTION_COLUMNS,
    build_training_dataset,
    chronological_split,
    compute_dataset_id,
    evaluate_predictions,
    fit_and_predict,
    read_price_table,
    run_training,
)

FIXED_NOW = datetime(2026, 1, 2, 12, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def training_prices() -> pd.DataFrame:
    dataset = generate_synthetic_dataset(SyntheticSettings(n_securities=5, n_days=260), seed=11)
    return dataset.prices


def test_read_price_table_supports_csv_and_parquet(
    tmp_path: Path, training_prices: pd.DataFrame
) -> None:
    csv_path = tmp_path / "prices.csv"
    parquet_path = tmp_path / "prices.parquet"
    training_prices.to_csv(csv_path, index=False)
    training_prices.to_parquet(parquet_path, index=False)

    csv_frame = read_price_table(csv_path)
    assert len(csv_frame) == len(training_prices)
    prepare_panel(csv_frame)
    pd.testing.assert_frame_equal(read_price_table(parquet_path), training_prices)


def test_read_price_table_reports_missing_and_unsupported_files(tmp_path: Path) -> None:
    with pytest.raises(TrainingError, match="not found"):
        read_price_table(tmp_path / "absent.csv")

    source = tmp_path / "prices.txt"
    source.write_text("security_id\n", encoding="utf-8")
    with pytest.raises(TrainingError, match="unsupported"):
        read_price_table(source)


def test_chronological_split_is_ordered_and_purges_overlapping_labels(
    training_prices: pd.DataFrame,
) -> None:
    dataset = build_training_dataset(prepare_panel(training_prices), [63])
    split = chronological_split(dataset, 0.7)

    assert split.train["date"].max() < split.test["date"].min()
    assert split.train["label_available_at"].max() <= split.test_start
    assert split.test_start == split.test_start_date.tz_localize("UTC") + pd.Timedelta(hours=21)
    assert split.purged_train_rows > 0
    assert split.test_start_date == split.test["date"].min()


def test_split_rejects_invalid_fraction_and_tiny_input() -> None:
    dataset = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01", "2024-01-02"]),
            "label_available_at": pd.to_datetime(["2024-01-03", "2024-01-04"], utc=True),
        }
    )

    with pytest.raises(TrainingError, match="between 0 and 1"):
        chronological_split(dataset, 1.0)
    with pytest.raises(TrainingError, match="at least two labeled trading dates"):
        chronological_split(dataset.iloc[[0]], 0.5)


def test_preprocessing_is_fit_on_training_rows_only(training_prices: pd.DataFrame) -> None:
    dataset = build_training_dataset(prepare_panel(training_prices), [21])
    split = chronological_split(dataset, 0.7)

    baseline = fit_and_predict("ridge", split.train, split.test, seed=7)
    altered_test = split.test.copy()
    altered_test[list(FEATURE_COLUMNS)] = altered_test[list(FEATURE_COLUMNS)] + 1_000.0
    altered = fit_and_predict("ridge", split.train, altered_test, seed=7)

    baseline_pipeline = cast(Pipeline, baseline.estimator)
    altered_pipeline = cast(Pipeline, altered.estimator)
    scaler_mean = baseline_pipeline.named_steps["scale"].mean_
    altered_mean = altered_pipeline.named_steps["scale"].mean_
    train_mean = split.train[list(FEATURE_COLUMNS)].mean().to_numpy()
    test_mean = split.test[list(FEATURE_COLUMNS)].mean().to_numpy()

    np.testing.assert_allclose(scaler_mean, train_mean, rtol=1e-10)
    np.testing.assert_allclose(scaler_mean, altered_mean, rtol=1e-10)
    assert not np.allclose(train_mean, test_mean)


def test_evaluate_predictions_reports_expected_metrics() -> None:
    frame = pd.DataFrame(
        {
            "date": pd.to_datetime(["2024-01-01"] * 3 + ["2024-01-02"] * 3),
            "predicted_excess_return": [0.1, 0.2, 0.3, 0.1, 0.2, 0.3],
            "realized_excess_return": [0.1, 0.2, 0.3, -0.3, 0.2, 0.1],
        }
    )

    metrics = evaluate_predictions(frame)

    assert metrics["n_predictions"] == 6
    assert metrics["n_dates"] == 2
    assert metrics["rmse"] == pytest.approx(np.sqrt((0.4**2 + 0.2**2) / 6))
    assert metrics["mae"] == pytest.approx(0.6 / 6)
    assert metrics["hit_rate"] == pytest.approx(5 / 6)
    assert metrics["mean_daily_rank_ic"] == pytest.approx(0.75)
    assert metrics["n_rank_ic_dates"] == 2


def test_run_training_writes_valid_predictions_and_manifest(
    tmp_path: Path, training_prices: pd.DataFrame
) -> None:
    result = run_training(
        training_prices,
        config=Config(),
        output_dir=tmp_path,
        dataset_path="synthetic/prices.csv",
        now=lambda: FIXED_NOW,
    )

    assert result.predictions_path.is_file()
    assert result.manifest_path.is_file()
    predictions = pd.read_parquet(result.predictions_path)
    manifest = json.loads(result.manifest_path.read_text(encoding="utf-8"))

    assert tuple(predictions.columns) == PREDICTION_COLUMNS
    assert set(predictions["model_id"]) == set(MODEL_IDS)
    assert set(predictions["horizon_days"]) == {21, 42, 63}
    assert predictions["prob_positive"].between(0.0, 1.0).all()
    assert (predictions["expected_volatility"] >= 0.0).all()
    assert np.allclose(
        predictions["realized_excess_return"],
        predictions["realized_forward_return"] - predictions["realized_benchmark_return"],
    )
    expected_as_of = predictions["date"].dt.tz_localize("UTC") + pd.Timedelta(hours=21)
    pd.testing.assert_series_equal(predictions["as_of"], expected_as_of, check_names=False)

    assert manifest["dataset"]["return_price_column"] == "close"
    assert manifest["dataset"]["sha256"] == compute_dataset_id(prepare_panel(training_prices))
    assert manifest["dataset"]["path"] == "synthetic/prices.csv"
    assert set(manifest["splits"]) == {"21", "42", "63"}
    for horizon, split in manifest["splits"].items():
        subset = predictions.loc[predictions["horizon_days"] == int(horizon)]
        assert subset["date"].min() >= pd.Timestamp(split["test_start_date"])
        assert split["train_end_date"] < split["test_start_date"]
        assert split["purged_train_rows"] > 0
        assert set(manifest["metrics"][horizon]) == set(MODEL_IDS)


def test_run_training_is_deterministic(tmp_path: Path, training_prices: pd.DataFrame) -> None:
    first = run_training(
        training_prices,
        config=Config(),
        output_dir=tmp_path / "first",
        now=lambda: FIXED_NOW,
    )
    second = run_training(
        training_prices,
        config=Config(),
        output_dir=tmp_path / "second",
        now=lambda: FIXED_NOW,
    )

    assert first.experiment_id == second.experiment_id
    assert first.metrics == second.metrics
    pd.testing.assert_frame_equal(
        pd.read_parquet(first.predictions_path),
        pd.read_parquet(second.predictions_path),
    )
    assert first.manifest_path.read_text(encoding="utf-8") == second.manifest_path.read_text(
        encoding="utf-8"
    )


def test_run_training_refuses_to_overwrite_an_experiment(
    tmp_path: Path, training_prices: pd.DataFrame
) -> None:
    run_training(training_prices, config=Config(), output_dir=tmp_path, now=lambda: FIXED_NOW)

    with pytest.raises(TrainingError, match="already exists"):
        run_training(training_prices, config=Config(), output_dir=tmp_path, now=lambda: FIXED_NOW)


def test_short_history_is_rejected(tmp_path: Path) -> None:
    dataset = generate_synthetic_dataset(SyntheticSettings(n_securities=3, n_days=80), seed=5)

    with pytest.raises(TrainingError, match="no rows have both complete features"):
        run_training(
            dataset.prices,
            config=Config(horizons=(63,)),
            output_dir=tmp_path,
            now=lambda: FIXED_NOW,
        )


def test_purge_can_remove_all_training_rows(tmp_path: Path) -> None:
    dataset = generate_synthetic_dataset(SyntheticSettings(n_securities=3, n_days=130), seed=5)

    with pytest.raises(TrainingError, match="no training rows remain"):
        run_training(
            dataset.prices,
            config=Config(horizons=(63,)),
            output_dir=tmp_path,
            now=lambda: FIXED_NOW,
        )


def test_cross_sectional_training_requires_two_securities() -> None:
    dataset = generate_synthetic_dataset(SyntheticSettings(n_securities=1, n_days=260), seed=5)

    with pytest.raises(TrainingError, match="at least two securities"):
        build_training_dataset(prepare_panel(dataset.prices), [21])
