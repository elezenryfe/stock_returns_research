"""Live provider smoke tests.

These make real network calls and are skipped unless the provider credential is
present. The offline suite never executes them.
"""

from __future__ import annotations

import os
from datetime import date

import pytest

from stock_returns.ingestion import TiingoProvider

pytestmark = pytest.mark.integration


@pytest.mark.skipif(not os.environ.get("TIINGO_API_KEY"), reason="TIINGO_API_KEY is not set")
def test_live_tiingo_returns_valid_bars() -> None:
    provider = TiingoProvider(os.environ["TIINGO_API_KEY"], timeout_seconds=30.0)

    batch = provider.fetch(["AAPL"], date(2024, 1, 2), date(2024, 1, 10))

    assert len(batch.bars) >= 4
    assert batch.bars["date"].is_monotonic_increasing
    assert bool((batch.bars["adj_close"] > 0).all())
    assert batch.securities.loc[0, "symbol"] == "AAPL"
