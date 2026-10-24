"""Schema and data-quality validation for normalized tabular datasets.

Validation functions raise `DataValidationError` containing every detected
problem (up to a configurable limit) rather than stopping at the first one.
They return a copy with date columns parsed; the input frame is not mutated.
"""

from __future__ import annotations

import numpy as np
import pandas as pd

from .errors import DataValidationError

PRICE_COLUMNS = ("security_id", "date", "open", "high", "low", "close", "volume")
SECURITY_COLUMNS = ("security_id", "ticker", "name", "exchange", "first_trade_date")
_TEXT_SECURITY_COLUMNS = ("security_id", "ticker", "name", "exchange")


def _examples(
    frame: pd.DataFrame, mask: pd.Series, columns: tuple[str, ...], limit: int = 3
) -> str:
    subset = frame.loc[mask, list(columns)].head(limit)
    return "; ".join(str(record) for record in subset.to_dict("records"))


def _raise_if_issues(issues: list[str], max_issues: int) -> None:
    if issues:
        if len(issues) >= max_issues:
            issues.append(f"at most {max_issues} issues are reported")
        raise DataValidationError(issues)


def _parse_dates(series: pd.Series) -> pd.Series:
    parsed = pd.Series(pd.to_datetime(series, errors="coerce", utc=True), name=series.name)
    return parsed.dt.tz_localize(None)


def validate_price_frame(
    prices: pd.DataFrame,
    *,
    min_price: float = 0.01,
    max_issues: int = 20,
    known_security_ids: set[str] | None = None,
) -> pd.DataFrame:
    """Validate daily price bars and return a copy with `date` parsed."""
    if prices.empty:
        raise DataValidationError(["price frame is empty"])
    missing = [column for column in PRICE_COLUMNS if column not in prices.columns]
    if missing:
        raise DataValidationError([f"missing required column(s): {', '.join(missing)}"])

    issues: list[str] = []

    def add(issue: str) -> None:
        if len(issues) < max_issues:
            issues.append(issue)

    references = ("security_id", "date")

    null_rows = prices[list(PRICE_COLUMNS)].isna().any(axis=1)
    if bool(null_rows.any()):
        null_columns = [c for c in PRICE_COLUMNS if bool(prices[c].isna().any())]
        add(
            f"{int(null_rows.sum())} row(s) contain null values in "
            f"{', '.join(null_columns)}; e.g. {_examples(prices, null_rows, references)}"
        )

    ids = prices["security_id"]
    empty_ids = ids.notna() & (ids.astype(str).str.strip() == "")
    if bool(empty_ids.any()):
        add(
            f"{int(empty_ids.sum())} blank security_id value(s); "
            f"e.g. {_examples(prices, empty_ids, references)}"
        )

    parsed_dates = _parse_dates(prices["date"])
    bad_dates = parsed_dates.isna()
    if bool(bad_dates.any()):
        add(
            f"column 'date' has {int(bad_dates.sum())} missing or unparseable value(s); "
            f"e.g. {_examples(prices, bad_dates, references)}"
        )

    numeric: dict[str, pd.Series] = {}
    for column in ("open", "high", "low", "close", "volume"):
        raw = prices[column]
        values = pd.to_numeric(raw, errors="coerce").astype(float)
        numeric[column] = values
        non_numeric = values.isna() & raw.notna()
        if bool(non_numeric.any()):
            add(
                f"column '{column}' has {int(non_numeric.sum())} non-numeric value(s); "
                f"e.g. {_examples(prices, non_numeric, references)}"
            )
        non_finite = values.notna() & ~np.isfinite(values)
        if bool(non_finite.any()):
            add(
                f"column '{column}' has {int(non_finite.sum())} non-finite value(s); "
                f"e.g. {_examples(prices, non_finite, references)}"
            )

    for column in ("open", "high", "low", "close"):
        below_min = numeric[column].notna() & (numeric[column] < min_price)
        if bool(below_min.any()):
            add(
                f"column '{column}' has {int(below_min.sum())} value(s) below "
                f"min_price={min_price}; e.g. {_examples(prices, below_min, references)}"
            )

    negative_volume = numeric["volume"].notna() & (numeric["volume"] < 0)
    if bool(negative_volume.any()):
        add(
            f"column 'volume' has {int(negative_volume.sum())} negative value(s); "
            f"e.g. {_examples(prices, negative_volume, references)}"
        )

    price_columns = ("open", "high", "low", "close")
    numeric_ok = pd.concat([numeric[c].notna() for c in price_columns], axis=1).all(axis=1)
    comparable = numeric_ok & ~bad_dates
    high_below_low = comparable & (numeric["high"] < numeric["low"])
    if bool(high_below_low.any()):
        add(
            f"{int(high_below_low.sum())} row(s) have high < low; "
            f"e.g. {_examples(prices, high_below_low, references)}"
        )
    high_below_body = comparable & (numeric["high"] < np.maximum(numeric["open"], numeric["close"]))
    if bool(high_below_body.any()):
        add(
            f"{int(high_below_body.sum())} row(s) have high < max(open, close); "
            f"e.g. {_examples(prices, high_below_body, references)}"
        )
    low_above_body = comparable & (numeric["low"] > np.minimum(numeric["open"], numeric["close"]))
    if bool(low_above_body.any()):
        add(
            f"{int(low_above_body.sum())} row(s) have low > min(open, close); "
            f"e.g. {_examples(prices, low_above_body, references)}"
        )

    duplicate_keys = pd.DataFrame({"security_id": ids, "date": parsed_dates})
    duplicates = duplicate_keys.duplicated(keep=False) & ids.notna() & parsed_dates.notna()
    if bool(duplicates.any()):
        add(
            f"{int(duplicates.sum())} duplicate (security_id, date) row(s); "
            f"e.g. {_examples(duplicate_keys, duplicates, references)}"
        )

    if known_security_ids is not None:
        observed = set(ids.dropna().astype(str))
        unknown = sorted(observed - known_security_ids)
        if unknown:
            add(
                f"{len(unknown)} security_id(s) missing from the securities table: "
                f"{', '.join(unknown[:5])}"
            )

    _raise_if_issues(issues, max_issues)

    result = prices.copy()
    result["date"] = parsed_dates
    return result


def validate_securities_frame(securities: pd.DataFrame, *, max_issues: int = 20) -> pd.DataFrame:
    """Validate the security master and return a copy with dates parsed."""
    if securities.empty:
        raise DataValidationError(["securities frame is empty"])
    missing = [column for column in SECURITY_COLUMNS if column not in securities.columns]
    if missing:
        raise DataValidationError([f"missing required column(s): {', '.join(missing)}"])

    issues: list[str] = []

    def add(issue: str) -> None:
        if len(issues) < max_issues:
            issues.append(issue)

    null_rows = securities[list(SECURITY_COLUMNS)].isna().any(axis=1)
    if bool(null_rows.any()):
        null_columns = [c for c in SECURITY_COLUMNS if bool(securities[c].isna().any())]
        add(
            f"{int(null_rows.sum())} row(s) contain null values in "
            f"{', '.join(null_columns)}; e.g. "
            f"{_examples(securities, null_rows, ('security_id',))}"
        )

    for column in _TEXT_SECURITY_COLUMNS:
        blank = securities[column].notna() & (securities[column].astype(str).str.strip() == "")
        if bool(blank.any()):
            add(
                f"column '{column}' has {int(blank.sum())} blank value(s); "
                f"e.g. {_examples(securities, blank, ('security_id',))}"
            )

    ids = securities["security_id"]
    duplicates = ids.notna() & ids.duplicated(keep=False)
    if bool(duplicates.any()):
        add(
            f"{int(duplicates.sum())} duplicate security_id value(s); "
            f"e.g. {_examples(securities, duplicates, ('security_id',))}"
        )

    first_dates = _parse_dates(securities["first_trade_date"])
    bad_first = first_dates.isna()
    if bool(bad_first.any()):
        add(
            f"column 'first_trade_date' has {int(bad_first.sum())} missing or "
            f"unparseable value(s); e.g. "
            f"{_examples(securities, bad_first, ('security_id', 'first_trade_date'))}"
        )

    last_dates = pd.Series(pd.NaT, index=securities.index, name="last_trade_date")
    if "last_trade_date" in securities.columns:
        last_dates = _parse_dates(securities["last_trade_date"])
        bad_last = last_dates.isna() & securities["last_trade_date"].notna()
        if bool(bad_last.any()):
            add(
                f"column 'last_trade_date' has {int(bad_last.sum())} unparseable value(s); "
                f"e.g. {_examples(securities, bad_last, ('security_id', 'last_trade_date'))}"
            )
        reversed_range = first_dates.notna() & last_dates.notna() & (last_dates < first_dates)
        if bool(reversed_range.any()):
            add(
                f"{int(reversed_range.sum())} row(s) have last_trade_date before "
                f"first_trade_date; e.g. {_examples(securities, reversed_range, ('security_id',))}"
            )

    _raise_if_issues(issues, max_issues)

    result = securities.copy()
    result["first_trade_date"] = first_dates
    result["last_trade_date"] = last_dates
    return result


def validate_dataset(
    prices: pd.DataFrame,
    securities: pd.DataFrame,
    *,
    min_price: float = 0.01,
    max_issues: int = 20,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate prices and the security master, including referential integrity.

    Returns ``(validated_prices, validated_securities)``.
    """
    validated_securities = validate_securities_frame(securities, max_issues=max_issues)
    known_ids = set(validated_securities["security_id"].astype(str))
    validated_prices = validate_price_frame(
        prices,
        min_price=min_price,
        max_issues=max_issues,
        known_security_ids=known_ids,
    )
    return validated_prices, validated_securities
