import pandas as pd
import pytest

from stock_returns.config import SyntheticSettings
from stock_returns.errors import DataValidationError
from stock_returns.synthetic import generate_synthetic_dataset
from stock_returns.validation import validate_dataset, validate_price_frame


def valid_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    dataset = generate_synthetic_dataset(SyntheticSettings(n_securities=3, n_days=10), seed=5)
    return dataset.prices.copy(), dataset.securities.copy()


def test_valid_prices_pass_and_dates_are_parsed() -> None:
    prices, securities = valid_frames()

    validated = validate_price_frame(
        prices, known_security_ids=set(securities["security_id"].astype(str))
    )

    assert pd.api.types.is_datetime64_any_dtype(validated["date"])
    assert len(validated) == len(prices)


def test_empty_price_frame_is_rejected() -> None:
    with pytest.raises(DataValidationError, match="price frame is empty"):
        validate_price_frame(pd.DataFrame())


def test_missing_column_is_reported() -> None:
    prices, _ = valid_frames()

    with pytest.raises(DataValidationError, match="missing required column"):
        validate_price_frame(prices.drop(columns=["volume"]))


def test_duplicate_rows_are_reported() -> None:
    prices, _ = valid_frames()
    duplicated = pd.concat([prices, prices.iloc[[0]]], ignore_index=True)

    with pytest.raises(DataValidationError, match="duplicate"):
        validate_price_frame(duplicated)


def test_negative_price_is_reported() -> None:
    prices, _ = valid_frames()
    prices.loc[0, "close"] = -1.0

    with pytest.raises(DataValidationError, match="below min_price"):
        validate_price_frame(prices)


def test_null_price_is_reported() -> None:
    prices, _ = valid_frames()
    prices.loc[0, "close"] = None

    with pytest.raises(DataValidationError, match="null values"):
        validate_price_frame(prices)


def test_non_numeric_price_is_reported() -> None:
    prices, _ = valid_frames()
    prices["close"] = prices["close"].astype(object)
    prices.loc[0, "close"] = "not-a-number"

    with pytest.raises(DataValidationError, match="non-numeric"):
        validate_price_frame(prices)


def test_high_below_low_is_reported() -> None:
    prices, _ = valid_frames()
    prices.loc[0, "high"] = prices.loc[0, "low"] - 1.0

    with pytest.raises(DataValidationError, match="high < low"):
        validate_price_frame(prices)


def test_unparseable_date_is_reported() -> None:
    prices, _ = valid_frames()
    prices["date"] = prices["date"].astype(str)
    prices.loc[0, "date"] = "not-a-date"

    with pytest.raises(DataValidationError, match="unparseable"):
        validate_price_frame(prices)


def test_unknown_security_is_reported() -> None:
    prices, securities = valid_frames()
    prices.loc[0, "security_id"] = "SEC9999"
    known = set(securities["security_id"].astype(str))

    with pytest.raises(DataValidationError, match="missing from the securities table"):
        validate_price_frame(prices, known_security_ids=known)


def test_min_price_is_configurable() -> None:
    prices, _ = valid_frames()
    prices.loc[0, ["open", "high", "low", "close"]] = 0.005

    validate_price_frame(prices, min_price=0.001)
    with pytest.raises(DataValidationError, match="below min_price"):
        validate_price_frame(prices, min_price=0.01)


def test_issue_reporting_is_capped() -> None:
    prices, _ = valid_frames()
    prices.loc[0, "close"] = -1.0

    with pytest.raises(DataValidationError, match="at most 1"):
        validate_price_frame(prices, max_issues=1)


def test_duplicate_security_id_is_reported() -> None:
    _, securities = valid_frames()
    securities.loc[1, "security_id"] = securities.loc[0, "security_id"]

    with pytest.raises(DataValidationError, match="duplicate security_id"):
        validate_dataset(valid_frames()[0], securities)


def test_blank_security_field_is_reported() -> None:
    _, securities = valid_frames()
    securities.loc[0, "ticker"] = " "

    with pytest.raises(DataValidationError, match="blank value"):
        validate_dataset(valid_frames()[0], securities)


def test_reversed_listing_window_is_reported() -> None:
    _, securities = valid_frames()
    securities.loc[0, "last_trade_date"] = "2000-01-01"

    with pytest.raises(DataValidationError, match="before"):
        validate_dataset(valid_frames()[0], securities)


def test_unparseable_listing_date_is_reported() -> None:
    _, securities = valid_frames()
    securities["first_trade_date"] = securities["first_trade_date"].astype(str)
    securities.loc[0, "first_trade_date"] = "not-a-date"

    with pytest.raises(DataValidationError, match="unparseable"):
        validate_dataset(valid_frames()[0], securities)
