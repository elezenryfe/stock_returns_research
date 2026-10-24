from pathlib import Path

import pytest

from stock_returns.config import Config, IngestionSettings, SyntheticSettings
from stock_returns.synthetic import SyntheticDataset, generate_synthetic_dataset

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "default.toml"


@pytest.fixture
def config_path() -> Path:
    return DEFAULT_CONFIG


@pytest.fixture
def synthetic_dataset() -> SyntheticDataset:
    return generate_synthetic_dataset(SyntheticSettings(n_securities=4, n_days=40), seed=11)


@pytest.fixture
def csv_source_dir(tmp_path: Path) -> Path:
    """A deterministic CSV source with three symbols of 12 business days."""
    dataset = generate_synthetic_dataset(SyntheticSettings(n_securities=3, n_days=12), seed=21)
    prices = dataset.prices.rename(columns={"security_id": "symbol"}).copy()
    prices["adj_close"] = prices["close"]
    prices["dividend_cash"] = 0.0
    prices["split_factor"] = 1.0
    prices = prices.loc[
        :,
        [
            "symbol",
            "date",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "adj_close",
            "dividend_cash",
            "split_factor",
        ],
    ]
    first = prices.groupby("symbol")["date"].min()
    last = prices.groupby("symbol")["date"].max()
    securities = dataset.securities.rename(columns={"security_id": "symbol"}).copy()
    securities["security_id"] = "csv-id-" + securities["symbol"]
    securities["first_data_date"] = securities["symbol"].map(first)
    securities["last_data_date"] = securities["symbol"].map(last)
    securities = securities.loc[
        :, ["symbol", "name", "exchange", "security_id", "first_data_date", "last_data_date"]
    ]

    source = tmp_path / "csv_source"
    source.mkdir()
    prices.to_csv(source / "prices.csv", index=False, date_format="%Y-%m-%d")
    securities.to_csv(source / "securities.csv", index=False, date_format="%Y-%m-%d")
    return source


@pytest.fixture
def ingestion_config(tmp_path: Path) -> Config:
    return Config(
        ingestion=IngestionSettings(
            raw_dir=tmp_path / "raw",
            normalized_dir=tmp_path / "normalized",
            max_attempts=2,
            retry_backoff_seconds=0.0,
            timeout_seconds=5.0,
        )
    )
