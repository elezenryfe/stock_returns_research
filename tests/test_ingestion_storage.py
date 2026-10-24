from datetime import UTC, date, datetime

import pandas as pd
import pytest

from stock_returns.errors import DataValidationError, ProviderError
from stock_returns.ingestion import CsvProvider, coverage_frame
from stock_returns.ingestion.normalize import (
    normalize_batch,
    validate_normalized_dataset,
)
from stock_returns.ingestion.storage import make_run_id, merge_normalized

START = date(2023, 1, 2)
END = date(2023, 12, 31)
FIRST = datetime(2024, 2, 1, 12, 0, tzinfo=UTC)
SECOND = datetime(2024, 2, 1, 12, 0, 1, tzinfo=UTC)


def make_frames(csv_source_dir):
    batch = CsvProvider(csv_source_dir).fetch(None, START, END)
    return normalize_batch(batch, run_id="run-1", ingested_at=FIRST)


def test_normalize_requires_aware_timestamp(csv_source_dir) -> None:
    batch = CsvProvider(csv_source_dir).fetch(None, START, END)

    with pytest.raises(ProviderError, match="timezone-aware"):
        normalize_batch(batch, run_id="run-1", ingested_at=datetime(2024, 1, 1))


def test_normalize_defaults_missing_security_ids(csv_source_dir) -> None:
    batch = CsvProvider(csv_source_dir).fetch(None, START, END)
    batch.securities["security_id"] = pd.NA

    prices, securities = normalize_batch(batch, run_id="run-1", ingested_at=FIRST)

    assert set(securities["security_id"]) == {
        "csv:SEC0001",
        "csv:SEC0002",
        "csv:SEC0003",
    }
    assert set(prices["security_id"]) == set(securities["security_id"])


def test_normalize_rejects_duplicate_security_ids(csv_source_dir) -> None:
    batch = CsvProvider(csv_source_dir).fetch(None, START, END)
    batch.securities.loc[1, "security_id"] = batch.securities.loc[0, "security_id"]

    with pytest.raises(ProviderError, match="multiple symbols map to the same security_id"):
        normalize_batch(batch, run_id="run-1", ingested_at=FIRST)


def test_validate_rejects_nonpositive_adjusted_close(csv_source_dir) -> None:
    prices, securities = make_frames(csv_source_dir)
    prices.loc[0, "adj_close"] = 0.0

    with pytest.raises(DataValidationError, match="adj_close"):
        validate_normalized_dataset(prices, securities)


def test_validate_rejects_nonpositive_split_factor(csv_source_dir) -> None:
    prices, securities = make_frames(csv_source_dir)
    prices.loc[0, "split_factor"] = 0.0

    with pytest.raises(DataValidationError, match="split_factor"):
        validate_normalized_dataset(prices, securities)


def test_validate_rejects_unknown_security_reference(csv_source_dir) -> None:
    prices, securities = make_frames(csv_source_dir)
    securities = securities.loc[securities["security_id"] != "csv-id-SEC0001"]

    with pytest.raises(DataValidationError, match="missing from the securities table"):
        validate_normalized_dataset(prices, securities)


def test_validate_rejects_duplicate_security_master_rows(csv_source_dir) -> None:
    prices, securities = make_frames(csv_source_dir)
    securities = pd.concat([securities, securities.iloc[[0]]], ignore_index=True)

    with pytest.raises(DataValidationError, match="duplicate security_id"):
        validate_normalized_dataset(prices, securities)


def test_merge_keeps_newest_revision_and_adds_no_rows(csv_source_dir) -> None:
    first_prices, first_securities = make_frames(csv_source_dir)
    batch = CsvProvider(csv_source_dir).fetch(None, START, END)
    second_prices, second_securities = normalize_batch(batch, run_id="run-2", ingested_at=SECOND)
    second_prices.loc[0, "adj_close"] = 123.456

    prices, _, rows_new = merge_normalized(
        first_prices, second_prices, first_securities, second_securities
    )

    assert rows_new == 0
    assert len(prices) == len(first_prices)
    key = (first_prices.loc[0, "security_id"], first_prices.loc[0, "date"])
    match = prices.loc[(prices["security_id"] == key[0]) & (prices["date"] == key[1])]
    assert len(match) == 1
    assert match.iloc[0]["adj_close"] == pytest.approx(123.456)
    assert match.iloc[0]["source_run_id"] == "run-2"


def test_make_run_id_is_request_and_time_dependent() -> None:
    first = make_run_id("csv", ("A", "B"), START, END, FIRST)
    same = make_run_id("csv", ("B", "A"), START, END, FIRST)
    later = make_run_id("csv", ("A", "B"), START, END, SECOND)
    other_symbol = make_run_id("csv", ("A", "C"), START, END, FIRST)

    assert first == same
    assert first != later
    assert first != other_symbol
    assert first.startswith("20240201T120000Z-")


def test_coverage_reports_missing_weekdays(csv_source_dir) -> None:
    prices, _ = make_frames(csv_source_dir)
    symbol_rows = prices.loc[prices["security_id"] == "csv-id-SEC0001"].copy()
    symbol_rows = symbol_rows.loc[symbol_rows["date"] != pd.Timestamp("2023-01-04")]

    report = coverage_frame(symbol_rows)

    assert len(report) == 1
    assert report.loc[0, "rows"] == 11
    assert report.loc[0, "missing_weekdays"] == 1
