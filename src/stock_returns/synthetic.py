"""Deterministic synthetic data for testing the infrastructure.

The series are artificial random walks. They exist only to exercise schemas,
validation, and later pipeline mechanics; they are not market data and say
nothing about predictive performance.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from .config import SyntheticSettings
from .seeding import make_rng
from .validation import PRICE_COLUMNS, SECURITY_COLUMNS


@dataclass(frozen=True)
class SyntheticDataset:
    securities: pd.DataFrame
    prices: pd.DataFrame


def generate_synthetic_dataset(settings: SyntheticSettings, *, seed: int) -> SyntheticDataset:
    """Generate a reproducible, internally consistent price panel."""
    rng = make_rng(seed)
    dates = pd.bdate_range(start=settings.start_date, periods=settings.n_days)
    n_days = len(dates)
    n_securities = settings.n_securities
    security_ids = [f"SEC{i:04d}" for i in range(1, n_securities + 1)]

    market = rng.normal(0.0002, 0.008, size=(n_days, 1))
    idiosyncratic = rng.normal(0.0001, 0.015, size=(n_days, n_securities))
    closes = 20.0 * np.exp(np.cumsum(market + idiosyncratic, axis=0))

    opens = closes * rng.normal(1.0, 0.002, size=(n_days, n_securities))
    spread = np.abs(rng.normal(0.006, 0.003, size=(n_days, n_securities)))
    highs = np.maximum(opens, closes) * (1.0 + spread)
    lows = np.minimum(opens, closes) * (1.0 - spread)
    volumes = rng.integers(100_000, 5_000_000, size=(n_days, n_securities))

    # Round first, then clamp the extremes so the OHLC invariants hold exactly.
    open_values = np.round(opens, 4)
    close_values = np.round(closes, 4)
    high_values = np.maximum(np.maximum(open_values, close_values), np.round(highs, 4))
    low_values = np.minimum(np.minimum(open_values, close_values), np.round(lows, 4))

    prices = pd.DataFrame(
        {
            "security_id": np.repeat(security_ids, n_days),
            "date": np.tile(dates.to_numpy(), n_securities),
            "open": open_values.T.reshape(-1),
            "high": high_values.T.reshape(-1),
            "low": low_values.T.reshape(-1),
            "close": close_values.T.reshape(-1),
            "volume": volumes.T.reshape(-1),
        },
        columns=list(PRICE_COLUMNS),
    )

    first_day = dates[0].date().isoformat()
    securities = pd.DataFrame(
        {
            "security_id": security_ids,
            "ticker": [f"SYN{i:03d}" for i in range(1, n_securities + 1)],
            "name": [f"Synthetic Corp {i}" for i in range(1, n_securities + 1)],
            "exchange": "XNYS",
            "first_trade_date": first_day,
            "last_trade_date": [None] * n_securities,
        },
        columns=list(SECURITY_COLUMNS) + ["last_trade_date"],
    )

    return SyntheticDataset(securities=securities, prices=prices)


def write_dataset(dataset: SyntheticDataset, out_dir: str | Path) -> tuple[Path, Path]:
    """Write `prices.csv` and `securities.csv`; return their paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    prices_path = out / "prices.csv"
    securities_path = out / "securities.csv"
    dataset.prices.to_csv(prices_path, index=False, date_format="%Y-%m-%d")
    dataset.securities.to_csv(securities_path, index=False)
    return prices_path, securities_path
