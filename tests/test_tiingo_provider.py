import json
import urllib.error
from datetime import UTC, date, datetime

import pandas as pd
import pytest

from stock_returns.config import Config
from stock_returns.errors import ProviderError
from stock_returns.ingestion import TiingoProvider, ingest, read_normalized
from stock_returns.ingestion.base import BAR_COLUMNS

START = date(2020, 1, 2)
END = date(2020, 1, 6)
RETRIEVED_AT = datetime(2024, 2, 1, 12, 0, tzinfo=UTC)


def metadata(symbol: str = "AAPL") -> dict[str, object]:
    return {
        "ticker": symbol,
        "name": "Apple Inc",
        "exchangeCode": "NASDAQ",
        "startDate": "1980-12-12T00:00:00.000Z",
        "endDate": "2020-01-06T00:00:00.000Z",
    }


def price_rows(symbol: str = "AAPL") -> list[dict[str, object]]:
    rows = []
    for offset, day in enumerate(("2020-01-02", "2020-01-03", "2020-01-06")):
        rows.append(
            {
                "date": f"{day}T00:00:00.000Z",
                "open": 100.0 + offset,
                "high": 102.0 + offset,
                "low": 99.0 + offset,
                "close": 101.0 + offset,
                "volume": 1000 + offset,
                "adjOpen": 100.5 + offset,
                "adjHigh": 102.5 + offset,
                "adjLow": 99.5 + offset,
                "adjClose": 101.5 + offset,
                "adjVolume": 1000 + offset,
                "divCash": 0.0,
                "splitFactor": 1.0,
                "ticker": symbol,
            }
        )
    return rows


def http_error(code: int) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://api.tiingo.com/example", code, f"status {code}", None, None
    )


def make_transport(responses, calls, sleeps):
    queue = list(responses)

    def transport(url, headers, timeout):
        calls.append({"url": url, "headers": headers, "timeout": timeout})
        item = queue.pop(0)
        if isinstance(item, Exception):
            raise item
        if isinstance(item, bytes):
            return item
        return json.dumps(item).encode("utf-8")

    return transport


def make_provider(responses, *, max_attempts: int = 3):
    calls: list[dict[str, object]] = []
    sleeps: list[float] = []
    provider = TiingoProvider(
        "test-key",
        transport=make_transport(responses, calls, sleeps),
        max_attempts=max_attempts,
        retry_backoff_seconds=0.5,
        timeout_seconds=5.0,
        sleep=sleeps.append,
    )
    return provider, calls, sleeps


def test_successful_fetch_and_auth_header() -> None:
    provider, calls, _ = make_provider([metadata(), price_rows()])

    batch = provider.fetch(["aapl"], START, END)

    assert batch.symbols == ("AAPL",)
    assert list(batch.bars.columns) == list(BAR_COLUMNS)
    assert len(batch.bars) == 3
    assert batch.bars["adj_close"].iloc[0] == pytest.approx(101.5)
    assert batch.securities.loc[0, "name"] == "Apple Inc"
    assert batch.securities.loc[0, "exchange"] == "NASDAQ"
    assert calls[0]["url"] == "https://api.tiingo.com/tiingo/daily/AAPL"
    assert "startDate=2020-01-02" in str(calls[1]["url"])
    assert "endDate=2020-01-06" in str(calls[1]["url"])
    assert calls[0]["headers"]["Authorization"] == "Token test-key"  # type: ignore[index]
    assert [payload.filename for payload in batch.payloads] == [
        "AAPL_metadata.json",
        "AAPL_prices.json",
    ]
    assert all("test-key" not in json.dumps(payload.request) for payload in batch.payloads)


def test_retries_transient_http_failure_then_succeeds() -> None:
    provider, calls, sleeps = make_provider([http_error(500), metadata(), price_rows()])

    batch = provider.fetch(["AAPL"], START, END)

    assert len(batch.bars) == 3
    assert len(calls) == 3
    assert sleeps == [0.5]


def test_retry_exhaustion_reports_provider_error() -> None:
    provider, calls, sleeps = make_provider([http_error(503), http_error(503), http_error(503)])

    with pytest.raises(ProviderError, match="HTTP 503"):
        provider.fetch(["AAPL"], START, END)
    assert len(calls) == 3
    assert sleeps == [0.5, 1.0]


def test_non_retryable_http_error_is_immediate() -> None:
    provider, calls, sleeps = make_provider([http_error(404)])

    with pytest.raises(ProviderError, match="HTTP 404"):
        provider.fetch(["AAPL"], START, END)
    assert len(calls) == 1
    assert sleeps == []


def test_missing_api_key_is_rejected() -> None:
    with pytest.raises(ProviderError, match="TIINGO_API_KEY"):
        TiingoProvider("  ")


def test_missing_symbols_is_rejected() -> None:
    provider, _, _ = make_provider([])

    with pytest.raises(ProviderError, match="explicit --symbols"):
        provider.fetch(None, START, END)


def test_invalid_json_is_rejected() -> None:
    provider, _, _ = make_provider([b"not-json"])

    with pytest.raises(ProviderError, match="invalid JSON"):
        provider.fetch(["AAPL"], START, END)


def test_missing_adjusted_close_is_rejected() -> None:
    rows = price_rows()
    del rows[0]["adjClose"]
    provider, _, _ = make_provider([metadata(), rows])

    with pytest.raises(ProviderError, match="adj_close"):
        provider.fetch(["AAPL"], START, END)


def test_out_of_range_bar_is_rejected() -> None:
    rows = price_rows()
    rows[0]["date"] = "2020-02-03T00:00:00.000Z"
    provider, _, _ = make_provider([metadata(), rows])

    with pytest.raises(ProviderError, match="outside the requested range"):
        provider.fetch(["AAPL"], START, END)


def test_empty_price_series_is_rejected() -> None:
    provider, _, _ = make_provider([metadata(), []])

    with pytest.raises(ProviderError, match="no daily prices"):
        provider.fetch(["AAPL"], START, END)


def test_error_messages_do_not_leak_api_key() -> None:
    provider, _, _ = make_provider([http_error(500), http_error(500), http_error(500)])

    with pytest.raises(ProviderError) as exc_info:
        provider.fetch(["AAPL"], START, END)
    assert "test-key" not in str(exc_info.value)


def test_pipeline_normalizes_tiingo_batch(ingestion_config: Config) -> None:
    provider, _, _ = make_provider([metadata(), price_rows()])

    result = ingest(
        provider,
        config=ingestion_config,
        symbols=["AAPL"],
        start=START,
        end=END,
        now=lambda: RETRIEVED_AT,
    )

    assert result.rows_received == 3
    assert result.rows_new == 3
    prices, securities = read_normalized(ingestion_config.ingestion.normalized_dir)
    assert set(prices["security_id"]) == {"tiingo:AAPL"}
    assert list(prices["date"]) == [
        pd.Timestamp("2020-01-02"),
        pd.Timestamp("2020-01-03"),
        pd.Timestamp("2020-01-06"),
    ]
    assert securities.loc[0, "first_data_date"] == pd.Timestamp("1980-12-12")
    assert securities.loc[0, "last_data_date"] == pd.Timestamp("2020-01-06")

    manifest = json.loads((result.raw_dir / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["provider"] == "tiingo"
    assert len(manifest["payloads"]) == 2
    assert "test-key" not in json.dumps(manifest)
