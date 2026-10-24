import pandas as pd
import pytest

from stock_returns.config import SyntheticSettings
from stock_returns.synthetic import (
    SyntheticDataset,
    generate_synthetic_dataset,
    write_dataset,
)
from stock_returns.validation import PRICE_COLUMNS, SECURITY_COLUMNS, validate_dataset


def test_schema_and_row_count(synthetic_dataset: SyntheticDataset) -> None:
    assert list(synthetic_dataset.prices.columns) == list(PRICE_COLUMNS)
    assert list(synthetic_dataset.securities.columns) == list(SECURITY_COLUMNS) + [
        "last_trade_date"
    ]
    assert len(synthetic_dataset.securities) == 4
    assert len(synthetic_dataset.prices) == 4 * 40


def test_generation_is_deterministic() -> None:
    settings = SyntheticSettings(n_securities=3, n_days=25)
    first = generate_synthetic_dataset(settings, seed=99)
    second = generate_synthetic_dataset(settings, seed=99)

    pd.testing.assert_frame_equal(first.prices, second.prices)
    pd.testing.assert_frame_equal(first.securities, second.securities)


def test_different_seeds_produce_different_data() -> None:
    settings = SyntheticSettings(n_securities=3, n_days=25)
    first = generate_synthetic_dataset(settings, seed=1)
    second = generate_synthetic_dataset(settings, seed=2)

    assert not first.prices["close"].equals(second.prices["close"])


def test_generated_prices_validate(synthetic_dataset: SyntheticDataset) -> None:
    validate_dataset(synthetic_dataset.prices, synthetic_dataset.securities)


def test_dates_are_business_days_only(synthetic_dataset: SyntheticDataset) -> None:
    dates = pd.to_datetime(synthetic_dataset.prices["date"]).drop_duplicates()

    assert not dates.dt.dayofweek.isin([5, 6]).any()
    assert len(dates) == 40


def test_round_trip_through_csv(tmp_path, synthetic_dataset: SyntheticDataset) -> None:
    prices_path, securities_path = write_dataset(synthetic_dataset, tmp_path / "out")

    prices = pd.read_csv(prices_path)
    securities = pd.read_csv(securities_path)
    validated_prices, validated_securities = validate_dataset(prices, securities)

    assert len(validated_prices) == len(synthetic_dataset.prices)
    assert len(validated_securities) == len(synthetic_dataset.securities)
    assert pd.api.types.is_datetime64_any_dtype(validated_prices["date"])


def test_invalid_seed_is_rejected() -> None:
    settings = SyntheticSettings(n_securities=2, n_days=5)

    with pytest.raises(ValueError, match="non-negative"):
        generate_synthetic_dataset(settings, seed=-1)
