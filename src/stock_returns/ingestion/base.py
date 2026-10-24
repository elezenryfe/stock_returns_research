"""Provider contract and raw batch types.

A provider fetches one request's worth of data and returns canonical bars plus
security metadata and the exact raw payloads. Normalization, validation,
storage, and provenance are handled centrally by the ingestion pipeline so that
every provider is subject to identical checks.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import date
from typing import Protocol, runtime_checkable

import pandas as pd

from ..errors import ProviderError

BAR_COLUMNS = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
    "dividend_cash",
    "split_factor",
)
SECURITY_COLUMNS = (
    "symbol",
    "name",
    "exchange",
    "security_id",
    "first_data_date",
    "last_data_date",
)

RETRYABLE_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})


@dataclass(frozen=True)
class RawPayload:
    """A provider response preserved verbatim for provenance."""

    filename: str
    content: bytes
    request: dict[str, str]


@dataclass(frozen=True)
class ProviderBatch:
    """Canonicalized provider output for one ingestion request."""

    provider: str
    symbols: tuple[str, ...]
    start: date
    end: date
    bars: pd.DataFrame
    securities: pd.DataFrame
    payloads: tuple[RawPayload, ...]

    def __post_init__(self) -> None:
        if not self.provider.strip():
            raise ProviderError("provider name must be non-empty")
        if not self.symbols:
            raise ProviderError("provider batch contains no symbols")
        if self.start > self.end:
            raise ProviderError("start date must not be after end date")
        if self.bars.empty:
            raise ProviderError("provider returned no price bars")
        if not self.payloads:
            raise ProviderError("provider returned no raw payloads")

        missing_bars = [column for column in BAR_COLUMNS if column not in self.bars.columns]
        if missing_bars:
            raise ProviderError(f"provider bars missing column(s): {', '.join(missing_bars)}")
        missing_securities = [
            column for column in SECURITY_COLUMNS if column not in self.securities.columns
        ]
        if missing_securities:
            raise ProviderError(
                f"provider securities missing column(s): {', '.join(missing_securities)}"
            )
        filenames = [payload.filename for payload in self.payloads]
        if len(set(filenames)) != len(filenames):
            raise ProviderError("provider payload filenames must be unique")


@runtime_checkable
class PriceProvider(Protocol):
    """Interface implemented by every price-data source."""

    name: str

    def fetch(self, symbols: Sequence[str] | None, start: date, end: date) -> ProviderBatch:
        """Fetch prices for `symbols` (None means all) over the inclusive range."""
        ...


def normalize_symbols(symbols: Sequence[str] | None) -> tuple[str, ...] | None:
    """Trim, uppercase, and de-duplicate requested symbols."""
    if symbols is None:
        return None
    cleaned = tuple(dict.fromkeys(symbol.strip().upper() for symbol in symbols if symbol.strip()))
    if not cleaned:
        raise ProviderError("at least one non-empty symbol is required")
    return cleaned
