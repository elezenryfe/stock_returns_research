import hashlib
import json
from datetime import UTC, date, datetime

import pandas as pd
import pytest

from stock_returns.config import Config
from stock_returns.errors import DataValidationError, ProviderError
from stock_returns.ingestion import CsvProvider, coverage_frame, ingest, read_normalized
from stock_returns.ingestion.base import BAR_COLUMNS

START = date(2023, 1, 2)
END = date(2023, 12, 31)
RETRIEVED_AT = datetime(2024, 2, 1, 12, 0, tzinfo=UTC)
RETRIEVED_LATER = datetime(2024, 2, 1, 12, 0, 1, tzinfo=UTC)


def test_csv_provider_returns_canonical_batch(csv_source_dir) -> None:
    batch = CsvProvider(csv_source_dir).fetch(None, START, END)

    assert batch.provider == "csv"
    assert batch.symbols == ("SEC0001", "SEC0002", "SEC0003")
    assert len(batch.bars) == 36
    assert list(batch.bars.columns) == list(BAR_COLUMNS)
    assert batch.securities["security_id"].str.startswith("csv-id-").all()
    assert len(batch.payloads) == 2


def test_ingest_writes_raw_and_normalized(csv_source_dir, ingestion_config: Config) -> None:
    result = ingest(
        CsvProvider(csv_source_dir),
        config=ingestion_config,
        symbols=None,
        start=START,
        end=END,
        now=lambda: RETRIEVED_AT,
    )

    assert result.rows_received == 36
    assert result.rows_new == 36
    assert result.rows_total == 36
    assert result.securities_count == 3

    prices, securities = read_normalized(ingestion_config.ingestion.normalized_dir)
    assert len(prices) == 36
    assert set(prices["security_id"]) == {"csv-id-SEC0001", "csv-id-SEC0002", "csv-id-SEC0003"}
    assert prices["ingested_at"].dt.tz is not None
    assert set(prices["source_run_id"]) == {result.run_id}
    assert securities["first_data_date"].dtype.kind == "M"

    manifest = json.loads((result.raw_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_id"] == result.run_id
    assert manifest["retrieved_at"] == RETRIEVED_AT.isoformat()
    assert manifest["rows"] == 36
    for payload in manifest["payloads"]:
        payload_path = result.raw_dir / payload["filename"]
        assert hashlib.sha256(payload_path.read_bytes()).hexdigest() == payload["sha256"]

    normalized_manifest = json.loads(
        (ingestion_config.ingestion.normalized_dir / "manifest.json").read_text(encoding="utf-8")
    )
    assert normalized_manifest["last_run_id"] == result.run_id
    assert normalized_manifest["rows"] == 36
    assert normalized_manifest["securities"] == 3


def test_reingest_is_idempotent(csv_source_dir, ingestion_config: Config) -> None:
    first = ingest(
        CsvProvider(csv_source_dir),
        config=ingestion_config,
        symbols=None,
        start=START,
        end=END,
        now=lambda: RETRIEVED_AT,
    )
    second = ingest(
        CsvProvider(csv_source_dir),
        config=ingestion_config,
        symbols=None,
        start=START,
        end=END,
        now=lambda: RETRIEVED_LATER,
    )

    assert second.run_id != first.run_id
    assert second.rows_received == 36
    assert second.rows_new == 0
    assert second.rows_total == 36

    prices, _ = read_normalized(ingestion_config.ingestion.normalized_dir)
    assert len(prices) == 36
    assert not prices.duplicated(subset=["security_id", "date"]).any()
    assert set(prices["source_run_id"]) == {second.run_id}
    assert (ingestion_config.ingestion.raw_dir / "csv" / first.run_id).is_dir()
    assert (ingestion_config.ingestion.raw_dir / "csv" / second.run_id).is_dir()


def test_symbol_and_date_filters(csv_source_dir, ingestion_config: Config) -> None:
    result = ingest(
        CsvProvider(csv_source_dir),
        config=ingestion_config,
        symbols=["sec0001"],
        start=date(2023, 1, 3),
        end=date(2023, 1, 9),
        now=lambda: RETRIEVED_AT,
    )

    assert result.rows_received == 5
    assert result.rows_new == 5
    assert result.securities_count == 1
    prices, _ = read_normalized(ingestion_config.ingestion.normalized_dir)
    assert set(prices["security_id"]) == {"csv-id-SEC0001"}
    assert prices["date"].min() == pd.Timestamp("2023-01-03")
    assert prices["date"].max() == pd.Timestamp("2023-01-09")


def test_unknown_symbol_is_rejected(csv_source_dir) -> None:
    with pytest.raises(ProviderError, match="absent from securities.csv"):
        CsvProvider(csv_source_dir).fetch(["NOPE"], START, END)


def test_missing_adj_close_is_rejected(csv_source_dir) -> None:
    prices_path = csv_source_dir / "prices.csv"
    prices = pd.read_csv(prices_path)
    prices.drop(columns=["adj_close"]).to_csv(prices_path, index=False)

    with pytest.raises(ProviderError, match="adj_close"):
        CsvProvider(csv_source_dir).fetch(None, START, END)


def test_duplicate_price_rows_are_rejected(csv_source_dir, ingestion_config: Config) -> None:
    prices_path = csv_source_dir / "prices.csv"
    prices = pd.read_csv(prices_path)
    pd.concat([prices, prices.iloc[[0]]], ignore_index=True).to_csv(prices_path, index=False)

    with pytest.raises(DataValidationError, match="duplicate"):
        ingest(
            CsvProvider(csv_source_dir),
            config=ingestion_config,
            symbols=None,
            start=START,
            end=END,
            now=lambda: RETRIEVED_AT,
        )
    assert not (ingestion_config.ingestion.normalized_dir / "prices.parquet").exists()


def test_failed_validation_leaves_normalized_data_untouched(
    csv_source_dir, ingestion_config: Config
) -> None:
    ingest(
        CsvProvider(csv_source_dir),
        config=ingestion_config,
        symbols=None,
        start=START,
        end=END,
        now=lambda: RETRIEVED_AT,
    )
    prices_path = ingestion_config.ingestion.normalized_dir / "prices.parquet"
    securities_path = ingestion_config.ingestion.normalized_dir / "securities.parquet"
    before = (
        hashlib.sha256(prices_path.read_bytes()).hexdigest(),
        hashlib.sha256(securities_path.read_bytes()).hexdigest(),
    )

    source_prices_path = csv_source_dir / "prices.csv"
    source_prices = pd.read_csv(source_prices_path)
    source_prices.loc[0, "close"] = -5.0
    source_prices.to_csv(source_prices_path, index=False)

    with pytest.raises(DataValidationError, match="below min_price"):
        ingest(
            CsvProvider(csv_source_dir),
            config=ingestion_config,
            symbols=None,
            start=START,
            end=END,
            now=lambda: RETRIEVED_LATER,
        )

    after = (
        hashlib.sha256(prices_path.read_bytes()).hexdigest(),
        hashlib.sha256(securities_path.read_bytes()).hexdigest(),
    )
    assert after == before


def test_coverage_summary(csv_source_dir, ingestion_config: Config) -> None:
    ingest(
        CsvProvider(csv_source_dir),
        config=ingestion_config,
        symbols=None,
        start=START,
        end=END,
        now=lambda: RETRIEVED_AT,
    )
    prices, _ = read_normalized(ingestion_config.ingestion.normalized_dir)
    report = coverage_frame(prices)

    assert len(report) == 3
    assert set(report["rows"]) == {12}
    assert set(report["missing_weekdays"]) == {0}
    assert report["first_date"].min() == pd.Timestamp("2023-01-02")
    assert report["last_date"].max() == pd.Timestamp("2023-01-17")
