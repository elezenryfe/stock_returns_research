"""Point-in-time features computed from daily price and volume panels.

A bar dated D is treated as available at 21:00 UTC on D, after the US regular
session closes in both EST and EDT. Every feature at D uses only bars dated D or
earlier.

The trading calendar is the sorted union of dates in the panel. Each security is
reindexed to that calendar before trailing windows are computed, so a window
counts trading days rather than observed rows. Missing bars are never
forward-filled: they make the affected windows missing until enough new
observations accumulate.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .errors import TrainingError

FEATURE_SET_VERSION = 1


@dataclass(frozen=True)
class FeatureSpec:
    """A single model input and the rule used to derive it."""

    name: str
    definition: str
    lookback_days: int


FEATURE_SPECS = (
    FeatureSpec(
        "momentum_5",
        "return over the trailing 5 trading days",
        5,
    ),
    FeatureSpec(
        "momentum_21",
        "return over the trailing 21 trading days",
        21,
    ),
    FeatureSpec(
        "momentum_63",
        "return over the trailing 63 trading days",
        63,
    ),
    FeatureSpec(
        "volatility_21",
        "sample standard deviation of daily returns over the trailing 21 trading days",
        21,
    ),
    FeatureSpec(
        "log_dollar_volume_21",
        "log1p of mean daily raw close times volume over the trailing 21 trading days",
        21,
    ),
    FeatureSpec(
        "market_relative_momentum_21",
        "momentum_21 minus the cross-sectional mean momentum_21 on the same date",
        21,
    ),
)
FEATURE_COLUMNS = tuple(spec.name for spec in FEATURE_SPECS)


@dataclass(frozen=True)
class PricePanel:
    """A validated, canonical price panel used for features and labels.

    ``frame`` has columns ``security_id``, ``date``, ``return_price``,
    ``close``, and ``volume``. ``return_price_column`` records whether the
    return series came from ``adj_close`` (preferred) or ``close``.
    """

    frame: pd.DataFrame
    return_price_column: str


def prepare_panel(prices: pd.DataFrame) -> PricePanel:
    """Validate a price table and select the return-price column.

    ``adj_close`` is used when present because total-return labels require
    split- and dividend-adjusted prices. The raw ``close`` is still required for
    dollar volume. Synthetic data has no corporate actions, so a documented
    fallback to ``close`` is permitted.
    """
    if not isinstance(prices, pd.DataFrame):
        raise TrainingError("prices must be a pandas DataFrame")
    if prices.empty:
        raise TrainingError("price frame is empty")

    required = ("security_id", "date", "close", "volume")
    missing = [column for column in required if column not in prices.columns]
    if missing:
        raise TrainingError(f"price frame missing required column(s): {', '.join(missing)}")

    return_price_column = "adj_close" if "adj_close" in prices.columns else "close"
    return_price_raw = prices[return_price_column]

    ids = prices["security_id"]
    if bool(ids.isna().any()):
        raise TrainingError("security_id contains missing values")
    ids = ids.astype(str).str.strip()
    if bool((ids == "").any()):
        raise TrainingError("security_id contains blank values")

    dates = (
        pd.Series(
            pd.to_datetime(prices["date"], errors="coerce", utc=True),
            index=prices.index,
        )
        .dt.tz_localize(None)
        .dt.normalize()
    )
    bad_dates = dates.isna()
    if bool(bad_dates.any()):
        raise TrainingError(f"date has {int(bad_dates.sum())} missing or unparseable value(s)")

    numeric: dict[str, pd.Series] = {}
    for column, raw in (
        ("return_price", return_price_raw),
        ("close", prices["close"]),
        ("volume", prices["volume"]),
    ):
        values = pd.to_numeric(raw, errors="coerce").astype(float)
        bad = values.isna() | ~np.isfinite(values)
        if bool(bad.any()):
            raise TrainingError(f"column '{column}' has {int(bad.sum())} non-numeric value(s)")
        numeric[column] = values

    for column in ("return_price", "close"):
        non_positive = numeric[column] <= 0.0
        if bool(non_positive.any()):
            raise TrainingError(
                f"column '{column}' has {int(non_positive.sum())} non-positive value(s)"
            )
    negative_volume = numeric["volume"] < 0.0
    if bool(negative_volume.any()):
        raise TrainingError(f"column 'volume' has {int(negative_volume.sum())} negative value(s)")

    frame = pd.DataFrame(
        {
            "security_id": ids,
            "date": dates,
            "return_price": numeric["return_price"],
            "close": numeric["close"],
            "volume": numeric["volume"],
        }
    )
    duplicate = frame.duplicated(subset=["security_id", "date"], keep=False)
    if bool(duplicate.any()):
        examples = frame.loc[duplicate, ["security_id", "date"]].head(3).to_dict("records")
        raise TrainingError(
            f"{int(duplicate.sum())} duplicate (security_id, date) row(s); e.g. {examples}"
        )

    frame = frame.sort_values(["security_id", "date"]).reset_index(drop=True)
    return PricePanel(frame=frame, return_price_column=return_price_column)


def compute_features(panel: PricePanel) -> pd.DataFrame:
    """Return a long point-in-time feature frame for every security and date.

    Rows are emitted even during the warm-up period, with ``NaN`` values. The
    training pipeline drops incomplete rows; nothing is imputed or filled here.
    """
    frame = panel.frame
    calendar = pd.DatetimeIndex(sorted(frame["date"].unique()))
    price = frame.pivot(index="date", columns="security_id", values="return_price")
    close = frame.pivot(index="date", columns="security_id", values="close")
    volume = frame.pivot(index="date", columns="security_id", values="volume")
    price = price.reindex(calendar).rename_axis(index="date", columns="security_id")
    close = close.reindex(calendar).rename_axis(index="date", columns="security_id")
    volume = volume.reindex(calendar).rename_axis(index="date", columns="security_id")

    daily_returns = price.pct_change(fill_method=None)
    momentum_21 = price.pct_change(21, fill_method=None)
    wide_features = {
        "momentum_5": price.pct_change(5, fill_method=None),
        "momentum_21": momentum_21,
        "momentum_63": price.pct_change(63, fill_method=None),
        "volatility_21": daily_returns.rolling(21, min_periods=21).std(),
        "log_dollar_volume_21": np.log1p((close * volume).rolling(21, min_periods=21).mean()),
        "market_relative_momentum_21": momentum_21.sub(momentum_21.mean(axis=1), axis=0),
    }

    long_frames: list[pd.DataFrame] = []
    for name, wide in wide_features.items():
        long_frames.append(
            wide.rename_axis(index="date", columns="security_id")
            .reset_index()
            .melt(id_vars="date", var_name="security_id", value_name=name)
        )
    features = long_frames[0]
    for other in long_frames[1:]:
        features = features.merge(
            other, on=["date", "security_id"], how="outer", validate="one_to_one"
        )

    features = features.replace([np.inf, -np.inf], np.nan)
    features = features.sort_values(["date", "security_id"]).reset_index(drop=True)
    return features.loc[:, ["security_id", "date", *FEATURE_COLUMNS]]
