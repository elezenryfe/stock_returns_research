"""Coverage summaries for the normalized dataset."""

from __future__ import annotations

import numpy as np
import pandas as pd

from ..errors import IngestionError


def coverage_frame(prices: pd.DataFrame) -> pd.DataFrame:
    """Per-security row counts, date bounds, and missing weekdays.

    ``missing_weekdays`` treats every weekday as a potential trading day, so
    exchange holidays are counted as missing. Use it to spot gaps, not as a
    precise trading-calendar audit.
    """
    if prices.empty:
        raise IngestionError("normalized price dataset is empty")
    missing_columns = [
        column for column in ("security_id", "symbol", "date") if column not in prices.columns
    ]
    if missing_columns:
        raise IngestionError(f"normalized prices missing column(s): {', '.join(missing_columns)}")

    grouped = prices.groupby("security_id", sort=True)
    report = grouped.agg(
        symbol=("symbol", "first"),
        rows=("date", "size"),
        first_date=("date", "min"),
        last_date=("date", "max"),
    ).reset_index()

    weekdays = np.busday_count(
        report["first_date"].values.astype("datetime64[D]"),
        (report["last_date"] + pd.Timedelta(days=1)).values.astype("datetime64[D]"),
    )
    report["missing_weekdays"] = weekdays - report["rows"]
    return report
