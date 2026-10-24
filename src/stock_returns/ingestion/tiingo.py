"""Tiingo end-of-day price adapter.

Authentication uses the ``Authorization: Token <key>`` header; the token never
appears in URLs, manifests, or error messages. Endpoints and fields follow the
public Tiingo documentation checked on 2026-10-24:

* ``GET /tiingo/daily/<ticker>`` — metadata (name, exchange, coverage dates).
* ``GET /tiingo/daily/<ticker>/prices?startDate=&endDate=&format=json`` —
  raw OHLCV, ``adjClose``, ``divCash``, and ``splitFactor``.

No live network call is made unless a provider is explicitly constructed.
"""

from __future__ import annotations

import json
import logging
import re
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable, Sequence
from datetime import date
from typing import Any

import pandas as pd

from ..errors import ProviderError
from .base import (
    BAR_COLUMNS,
    RETRYABLE_HTTP_STATUS,
    SECURITY_COLUMNS,
    ProviderBatch,
    RawPayload,
    normalize_symbols,
)

logger = logging.getLogger(__name__)

Transport = Callable[[str, dict[str, str], float], bytes]
_BASE_URL = "https://api.tiingo.com/tiingo/daily"
_REQUIRED_PRICE_FIELDS = ("date", "open", "high", "low", "close", "volume", "adjClose")


def _urllib_get(url: str, headers: dict[str, str], timeout: float) -> bytes:
    request = urllib.request.Request(url, headers=headers, method="GET")
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


class TiingoProvider:
    """Fetch daily bars from Tiingo for an explicit symbol list."""

    name = "tiingo"

    def __init__(
        self,
        api_key: str,
        *,
        transport: Transport | None = None,
        max_attempts: int = 3,
        retry_backoff_seconds: float = 0.5,
        timeout_seconds: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not api_key or not api_key.strip():
            raise ProviderError(
                "TIINGO_API_KEY is not set; export it in the environment before ingesting"
            )
        if max_attempts < 1:
            raise ProviderError("max_attempts must be at least 1")
        if retry_backoff_seconds < 0:
            raise ProviderError("retry_backoff_seconds must be non-negative")
        if timeout_seconds <= 0:
            raise ProviderError("timeout_seconds must be positive")
        self._api_key = api_key.strip()
        self._transport = transport or _urllib_get
        self._max_attempts = max_attempts
        self._retry_backoff_seconds = retry_backoff_seconds
        self._timeout_seconds = timeout_seconds
        self._sleep = sleep

    def fetch(self, symbols: Sequence[str] | None, start: date, end: date) -> ProviderBatch:
        requested = normalize_symbols(symbols)
        if requested is None:
            raise ProviderError("the tiingo provider requires an explicit --symbols list")
        if start > end:
            raise ProviderError("start date must not be after end date")

        bar_frames: list[pd.DataFrame] = []
        security_rows: list[dict[str, Any]] = []
        payloads: list[RawPayload] = []

        for symbol in requested:
            metadata_payload, metadata_raw = self._get_json(
                f"{_BASE_URL}/{urllib.parse.quote(symbol, safe='')}",
                context=f"metadata for {symbol}",
            )
            metadata = self._metadata_object(metadata_payload, symbol)

            query = urllib.parse.urlencode(
                {
                    "startDate": start.isoformat(),
                    "endDate": end.isoformat(),
                    "format": "json",
                }
            )
            prices_payload, prices_raw = self._get_json(
                f"{_BASE_URL}/{urllib.parse.quote(symbol, safe='')}/prices?{query}",
                context=f"daily prices for {symbol}",
            )
            bars = self._price_frame(prices_payload, symbol, start, end)
            bar_frames.append(bars)

            first_date = (
                self._optional_date(metadata.get("startDate"), symbol) or bars["date"].min()
            )
            last_date = self._optional_date(metadata.get("endDate"), symbol) or bars["date"].max()
            security_rows.append(
                {
                    "symbol": symbol,
                    "name": str(metadata.get("name") or symbol),
                    "exchange": str(metadata.get("exchangeCode") or "UNKNOWN"),
                    "security_id": pd.NA,
                    "first_data_date": first_date,
                    "last_data_date": last_date,
                }
            )

            safe_symbol = re.sub(r"[^A-Za-z0-9._-]", "_", symbol)
            payloads.extend(
                [
                    RawPayload(
                        filename=f"{safe_symbol}_metadata.json",
                        content=metadata_raw,
                        request={"endpoint": f"daily/{symbol}"},
                    ),
                    RawPayload(
                        filename=f"{safe_symbol}_prices.json",
                        content=prices_raw,
                        request={
                            "endpoint": f"daily/{symbol}/prices",
                            "startDate": start.isoformat(),
                            "endDate": end.isoformat(),
                            "format": "json",
                        },
                    ),
                ]
            )

        bars = pd.concat(bar_frames, ignore_index=True)
        bars = bars.sort_values(["symbol", "date"]).reset_index(drop=True)
        securities = pd.DataFrame(security_rows)
        securities["first_data_date"] = pd.to_datetime(
            securities["first_data_date"], utc=True
        ).dt.tz_localize(None)
        securities["last_data_date"] = pd.to_datetime(
            securities["last_data_date"], utc=True
        ).dt.tz_localize(None)
        securities = securities.loc[:, list(SECURITY_COLUMNS)]

        return ProviderBatch(
            provider=self.name,
            symbols=requested,
            start=start,
            end=end,
            bars=bars.loc[:, list(BAR_COLUMNS)],
            securities=securities,
            payloads=tuple(payloads),
        )

    def _request(self, url: str, context: str) -> bytes:
        headers = {
            "Authorization": f"Token {self._api_key}",
            "Accept": "application/json",
            "User-Agent": "stock-returns-research/0.2",
        }
        for attempt in range(1, self._max_attempts + 1):
            try:
                return self._transport(url, headers, self._timeout_seconds)
            except urllib.error.HTTPError as exc:
                if exc.code not in RETRYABLE_HTTP_STATUS or attempt == self._max_attempts:
                    raise ProviderError(
                        f"Tiingo request failed for {context}: HTTP {exc.code} {exc.msg}"
                    ) from exc
                reason = f"HTTP {exc.code}"
            except OSError as exc:
                if attempt == self._max_attempts:
                    raise ProviderError(f"Tiingo request failed for {context}: {exc}") from exc
                reason = str(exc)

            delay = self._retry_backoff_seconds * (2 ** (attempt - 1))
            logger.warning(
                "Tiingo %s attempt %d/%d failed (%s); retrying in %.2fs",
                context,
                attempt,
                self._max_attempts,
                reason,
                delay,
            )
            self._sleep(delay)

        raise AssertionError("unreachable: retry loop must return or raise")

    def _get_json(self, url: str, *, context: str) -> tuple[Any, bytes]:
        raw = self._request(url, context)
        try:
            payload = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise ProviderError(f"Tiingo returned invalid JSON for {context}") from exc
        return payload, raw

    @staticmethod
    def _metadata_object(payload: Any, symbol: str) -> dict[str, Any]:
        if isinstance(payload, list) and len(payload) == 1:
            payload = payload[0]
        if not isinstance(payload, dict):
            raise ProviderError(f"Tiingo metadata for {symbol} is not a JSON object")
        return payload

    def _price_frame(self, payload: Any, symbol: str, start: date, end: date) -> pd.DataFrame:
        if not isinstance(payload, list) or not payload:
            raise ProviderError(
                f"Tiingo returned no daily prices for {symbol} between "
                f"{start.isoformat()} and {end.isoformat()}"
            )
        if not all(isinstance(record, dict) for record in payload):
            raise ProviderError(f"Tiingo price records for {symbol} are malformed")

        frame = pd.DataFrame(payload)
        missing = [field for field in _REQUIRED_PRICE_FIELDS if field not in frame.columns]
        if missing:
            raise ProviderError(
                f"Tiingo price records for {symbol} missing field(s): {', '.join(missing)}"
            )

        frame = frame.rename(
            columns={
                "adjClose": "adj_close",
                "divCash": "dividend_cash",
                "splitFactor": "split_factor",
            }
        )
        frame["symbol"] = symbol
        frame["date"] = (
            pd.Series(pd.to_datetime(frame["date"], errors="coerce", utc=True), name="date")
            .dt.tz_localize(None)
            .dt.normalize()
        )
        if bool(frame["date"].isna().any()):
            raise ProviderError(f"Tiingo returned unparseable dates for {symbol}")

        for column in ("open", "high", "low", "close", "volume", "adj_close"):
            frame[column] = pd.to_numeric(frame[column], errors="coerce").astype(float)
            if bool(frame[column].isna().any()):
                raise ProviderError(
                    f"Tiingo returned missing or non-numeric '{column}' for {symbol}"
                )

        for column, default in (("dividend_cash", 0.0), ("split_factor", 1.0)):
            if column not in frame.columns:
                frame[column] = default
            else:
                raw = frame[column]
                frame[column] = pd.to_numeric(raw, errors="coerce").astype(float)
                bad = frame[column].isna() & raw.notna()
                if bool(bad.any()):
                    raise ProviderError(f"Tiingo returned non-numeric '{column}' for {symbol}")
                frame[column] = frame[column].fillna(default)

        outside = ~frame["date"].between(pd.Timestamp(start), pd.Timestamp(end))
        if bool(outside.any()):
            raise ProviderError(
                f"Tiingo returned {int(outside.sum())} row(s) outside the requested range "
                f"for {symbol}"
            )

        return frame.sort_values("date").reset_index(drop=True)

    @staticmethod
    def _optional_date(value: object, symbol: str) -> pd.Timestamp | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            parsed = pd.Timestamp(value)
        except (TypeError, ValueError) as exc:
            raise ProviderError(f"Tiingo metadata for {symbol} has an invalid date") from exc
        if pd.isna(parsed):
            return None
        if parsed.tzinfo is not None:
            parsed = parsed.tz_convert("UTC").tz_localize(None)
        return parsed.normalize()
