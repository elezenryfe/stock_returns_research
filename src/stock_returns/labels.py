"""Forward-return labels and the equal-weight research benchmark.

Conventions:

* The trading calendar is the sorted union of dates in the price panel.
* The forward return at date D for horizon h is
  ``return_price(D + h) / return_price(D) - 1`` where ``D + h`` is the h-th
  following trading day on that calendar. A missing bar is not forward-filled,
  so the label is missing if either endpoint is unavailable.
* The benchmark at D is the equal-weight mean of the same-horizon forward
  returns of every security with a valid label at D. The target is
  ``excess_return = forward_return - benchmark_return``.
* A bar dated D is treated as available at 21:00 UTC on D. A label is available
  at 21:00 UTC on its end date.

Labels are forward-looking by construction. They may only be used as training
targets and evaluation outcomes, never as model inputs.
"""

from __future__ import annotations

from collections.abc import Sequence

import pandas as pd

from .errors import TrainingError
from .features import PricePanel

MARKET_CLOSE_UTC_HOUR = 21


def market_close_timestamps(dates: pd.Series) -> pd.Series:
    """Map naive exchange trading dates to UTC market-close timestamps."""
    values = pd.to_datetime(pd.Series(dates), errors="coerce")
    if bool(values.isna().any()):
        raise TrainingError("label dates contain missing or unparseable values")
    if values.dt.tz is not None:
        values = values.dt.tz_convert("UTC").dt.tz_localize(None)
    return (values.dt.normalize() + pd.Timedelta(hours=MARKET_CLOSE_UTC_HOUR)).dt.tz_localize("UTC")


def market_close_timestamp(day: pd.Timestamp) -> pd.Timestamp:
    """Map one naive exchange trading date to its UTC market-close timestamp."""
    return market_close_timestamps(pd.Series([day])).iloc[0]


def compute_labels(panel: PricePanel, horizons: Sequence[int]) -> pd.DataFrame:
    """Build long-form forward-return labels for every security, date, horizon."""
    ordered = list(horizons)
    if not ordered:
        raise TrainingError("at least one forecast horizon is required")
    if len(set(ordered)) != len(ordered):
        raise TrainingError("forecast horizons must not contain duplicates")
    for horizon in ordered:
        if isinstance(horizon, bool) or not isinstance(horizon, int) or horizon <= 0:
            raise TrainingError(f"forecast horizons must be positive integers, got {horizon!r}")

    frame = panel.frame
    calendar = pd.DatetimeIndex(sorted(frame["date"].unique()))
    price = (
        frame.pivot(index="date", columns="security_id", values="return_price")
        .reindex(calendar)
        .rename_axis(index="date", columns="security_id")
    )

    frames: list[pd.DataFrame] = []
    for horizon in ordered:
        if horizon >= len(calendar):
            raise TrainingError(
                f"horizon {horizon} requires more than the {len(calendar)} available trading days"
            )

        forward = price.shift(-horizon) / price - 1.0
        benchmark = forward.mean(axis=1)
        label_dates = pd.Series(calendar, index=calendar).shift(-horizon)

        labels = (
            forward.reset_index()
            .melt(id_vars="date", var_name="security_id", value_name="forward_return")
            .dropna(subset=["forward_return"])
        )
        labels["label_date"] = labels["date"].map(label_dates)
        labels = labels.dropna(subset=["label_date"])
        labels["horizon_days"] = horizon
        labels["benchmark_return"] = benchmark.reindex(labels["date"]).to_numpy()
        labels["excess_return"] = labels["forward_return"] - labels["benchmark_return"]
        labels["as_of"] = market_close_timestamps(labels["date"])
        labels["label_available_at"] = market_close_timestamps(labels["label_date"])
        frames.append(labels.drop(columns=["label_date"]))

    combined = pd.concat(frames, ignore_index=True)
    combined = combined.loc[
        :,
        [
            "security_id",
            "date",
            "as_of",
            "horizon_days",
            "label_available_at",
            "forward_return",
            "benchmark_return",
            "excess_return",
        ],
    ]
    combined = combined.sort_values(["horizon_days", "date", "security_id"]).reset_index(drop=True)
    if bool((combined["label_available_at"] <= combined["as_of"]).any()):
        raise TrainingError("label availability must be after as_of")
    return combined
