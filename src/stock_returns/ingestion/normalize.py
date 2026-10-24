"""Normalize provider output into the canonical ingested schema."""

from __future__ import annotations

from datetime import UTC, datetime

import numpy as np
import pandas as pd

from ..errors import DataValidationError, ProviderError
from ..validation import validate_price_frame
from .base import ProviderBatch

NORMALIZED_PRICE_COLUMNS = (
    "security_id",
    "symbol",
    "provider",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
    "dividend_cash",
    "split_factor",
    "source_run_id",
    "ingested_at",
)
NORMALIZED_SECURITY_COLUMNS = (
    "security_id",
    "symbol",
    "name",
    "exchange",
    "provider",
    "first_data_date",
    "last_data_date",
)


def default_security_id(provider: str, symbol: str) -> str:
    """Provisional ticker-based identifier; not a permanent security master id."""
    return f"{provider}:{symbol}"


def normalize_batch(
    batch: ProviderBatch, *, run_id: str, ingested_at: datetime
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Add stable identifiers and provenance to a provider batch."""
    if ingested_at.tzinfo is None or ingested_at.utcoffset() is None:
        raise ProviderError("ingested_at must be timezone-aware")
    ingested_at_utc = ingested_at.astimezone(UTC)

    securities = batch.securities.copy()
    securities["symbol"] = securities["symbol"].astype(str).str.strip().str.upper()
    if bool((securities["symbol"] == "").any()):
        raise ProviderError("provider securities contain blank symbols")
    if bool(securities["symbol"].duplicated().any()):
        raise ProviderError("provider returned duplicate security symbols")

    provided_ids = securities["security_id"].astype("string").str.strip()
    provided_ids = provided_ids.mask(provided_ids == "")
    ids = [
        str(provided).strip() if provided is not pd.NA and pd.notna(provided) else None
        for provided in provided_ids
    ]
    securities["security_id"] = [
        security_id if security_id else default_security_id(batch.provider, symbol)
        for security_id, symbol in zip(ids, securities["symbol"], strict=True)
    ]
    if bool(securities["security_id"].duplicated().any()):
        raise ProviderError(
            "multiple symbols map to the same security_id; multi-ticker security "
            "histories are not supported yet"
        )

    for column in ("first_data_date", "last_data_date"):
        parsed = (
            pd.Series(
                pd.to_datetime(securities[column], errors="coerce", utc=True),
                index=securities.index,
            )
            .dt.tz_localize(None)
            .dt.normalize()
        )
        if bool(parsed.isna().any()):
            raise ProviderError(f"provider securities have missing {column} values")
        securities[column] = parsed
    reversed_range = securities["last_data_date"] < securities["first_data_date"]
    if bool(reversed_range.any()):
        raise ProviderError("provider securities have last_data_date before first_data_date")
    securities["provider"] = batch.provider

    bars = batch.bars.copy()
    bars["symbol"] = bars["symbol"].astype(str).str.strip().str.upper()
    if bool((bars["symbol"] == "").any()):
        raise ProviderError("provider price bars contain blank symbols")
    unknown = sorted(set(bars["symbol"]) - set(securities["symbol"]))
    if unknown:
        raise ProviderError(
            "price bars reference symbols missing from securities metadata: "
            + ", ".join(unknown[:10])
        )
    id_by_symbol = dict(zip(securities["symbol"], securities["security_id"], strict=True))
    bars["security_id"] = bars["symbol"].map(id_by_symbol)

    bars["date"] = (
        pd.Series(pd.to_datetime(bars["date"], errors="coerce", utc=True), index=bars.index)
        .dt.tz_localize(None)
        .dt.normalize()
    )
    if bool(bars["date"].isna().any()):
        raise ProviderError("provider price bars contain unparseable dates")
    outside = ~bars["date"].between(pd.Timestamp(batch.start), pd.Timestamp(batch.end))
    if bool(outside.any()):
        raise ProviderError(
            f"provider returned {int(outside.sum())} price bar(s) outside the requested range"
        )

    bars["provider"] = batch.provider
    bars["source_run_id"] = run_id
    bars["ingested_at"] = pd.Timestamp(ingested_at_utc)
    for column in ("provider", "source_run_id"):
        if bool((bars[column].astype(str).str.strip() == "").any()):
            raise ProviderError(f"normalized column '{column}' must be non-empty")

    prices = (
        bars.loc[:, list(NORMALIZED_PRICE_COLUMNS)]
        .sort_values(["security_id", "date"])
        .reset_index(drop=True)
    )
    securities = (
        securities.loc[:, list(NORMALIZED_SECURITY_COLUMNS)]
        .sort_values("security_id")
        .reset_index(drop=True)
    )
    return prices, securities


def validate_normalized_dataset(
    prices: pd.DataFrame,
    securities: pd.DataFrame,
    *,
    min_price: float = 0.01,
    max_issues: int = 20,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Validate the canonical ingested dataset, including adjusted prices."""
    security_issues: list[str] = []
    missing = [column for column in NORMALIZED_SECURITY_COLUMNS if column not in securities.columns]
    if missing:
        raise DataValidationError(
            [f"normalized securities missing column(s): {', '.join(missing)}"]
        )
    if securities.empty:
        raise DataValidationError(["normalized securities frame is empty"])

    null_columns = [
        column for column in NORMALIZED_SECURITY_COLUMNS if securities[column].isna().any()
    ]
    if null_columns:
        security_issues.append(
            f"normalized securities contain null values in: {', '.join(null_columns)}"
        )
    duplicate_ids = securities["security_id"].duplicated(keep=False)
    if bool(duplicate_ids.any()):
        security_issues.append(
            f"{int(duplicate_ids.sum())} duplicate security_id value(s) in securities"
        )
    first_dates = pd.Series(
        pd.to_datetime(securities["first_data_date"], errors="coerce", utc=True),
        index=securities.index,
    ).dt.tz_localize(None)
    last_dates = pd.Series(
        pd.to_datetime(securities["last_data_date"], errors="coerce", utc=True),
        index=securities.index,
    ).dt.tz_localize(None)
    bad_dates = first_dates.isna() | last_dates.isna()
    if bool(bad_dates.any()):
        security_issues.append(
            f"{int(bad_dates.sum())} security row(s) have unparseable coverage dates"
        )
    reversed_range = first_dates.notna() & last_dates.notna() & (last_dates < first_dates)
    if bool(reversed_range.any()):
        security_issues.append(
            f"{int(reversed_range.sum())} security row(s) have last_data_date before "
            "first_data_date"
        )
    if security_issues:
        raise DataValidationError(security_issues)

    securities = securities.copy()
    securities["first_data_date"] = first_dates
    securities["last_data_date"] = last_dates

    issues: list[str] = []
    try:
        prices = validate_price_frame(
            prices,
            min_price=min_price,
            max_issues=max_issues,
            known_security_ids=set(securities["security_id"].astype(str)),
        )
    except DataValidationError as exc:
        issues.extend(exc.issues)
        prices = prices.copy()

    for column, message in (
        ("adj_close", "must be > 0"),
        ("split_factor", "must be > 0"),
    ):
        values = pd.to_numeric(prices[column], errors="coerce").astype(float)
        invalid = values.isna() | ~np.isfinite(values) | (values <= 0.0)
        if bool(invalid.any()):
            issues.append(f"column '{column}' has {int(invalid.sum())} invalid value(s); {message}")
    dividend = pd.to_numeric(prices["dividend_cash"], errors="coerce").astype(float)
    invalid_dividend = dividend.isna() | ~np.isfinite(dividend)
    if bool(invalid_dividend.any()):
        issues.append(
            f"column 'dividend_cash' has {int(invalid_dividend.sum())} non-finite value(s)"
        )

    ingested = pd.to_datetime(prices["ingested_at"], errors="coerce", utc=True)
    if bool(ingested.isna().any()):
        issues.append(f"column 'ingested_at' has {int(ingested.isna().sum())} unparseable value(s)")
    for column in ("provider", "symbol", "source_run_id"):
        blank = prices[column].isna() | (prices[column].astype(str).str.strip() == "")
        if bool(blank.any()):
            issues.append(f"column '{column}' has {int(blank.sum())} blank value(s)")

    if issues:
        raise DataValidationError(issues)

    prices = prices.copy()
    prices["adj_close"] = pd.to_numeric(prices["adj_close"]).astype(float)
    prices["dividend_cash"] = pd.to_numeric(prices["dividend_cash"]).astype(float)
    prices["split_factor"] = pd.to_numeric(prices["split_factor"]).astype(float)
    prices["ingested_at"] = ingested
    return prices, securities
