"""Local CSV source adapter.

Expected input directory:

* ``prices.csv`` — ``symbol,date,open,high,low,close,volume,adj_close`` and
  optional ``dividend_cash,split_factor`` columns.
* ``securities.csv`` — ``symbol,name,exchange`` and optional
  ``security_id,first_data_date,last_data_date`` columns.

Dates are ISO ``YYYY-MM-DD`` trading dates. ``adj_close`` is required because
return calculations must use split- and dividend-adjusted prices. This adapter
ingests licensed bulk exports or hand-curated files; it never downloads data.
"""

from __future__ import annotations

import io
from collections.abc import Sequence
from datetime import date
from pathlib import Path

import pandas as pd

from ..errors import ProviderError
from .base import BAR_COLUMNS, SECURITY_COLUMNS, ProviderBatch, RawPayload, normalize_symbols

_REQUIRED_PRICE_COLUMNS = (
    "symbol",
    "date",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "adj_close",
)
_REQUIRED_SECURITY_COLUMNS = ("symbol", "name", "exchange")


class CsvProvider:
    """Read canonical price/security CSVs from a local directory."""

    name = "csv"

    def __init__(self, input_dir: str | Path) -> None:
        self.input_dir = Path(input_dir)
        if not self.input_dir.is_dir():
            raise ProviderError(f"CSV input directory not found: {self.input_dir}")

    def fetch(self, symbols: Sequence[str] | None, start: date, end: date) -> ProviderBatch:
        if start > end:
            raise ProviderError("start date must not be after end date")
        requested = normalize_symbols(symbols)

        prices_path = self.input_dir / "prices.csv"
        securities_path = self.input_dir / "securities.csv"
        prices_raw = self._read_bytes(prices_path)
        securities_raw = self._read_bytes(securities_path)
        prices = self._read_csv(prices_raw, prices_path, dtype={"symbol": str})
        securities = self._read_csv(
            securities_raw,
            securities_path,
            dtype={"symbol": str, "security_id": str},
        )

        missing = [column for column in _REQUIRED_PRICE_COLUMNS if column not in prices.columns]
        if missing:
            raise ProviderError(f"prices.csv missing required column(s): {', '.join(missing)}")
        missing = [
            column for column in _REQUIRED_SECURITY_COLUMNS if column not in securities.columns
        ]
        if missing:
            raise ProviderError(f"securities.csv missing required column(s): {', '.join(missing)}")

        prices = prices.copy()
        securities = securities.copy()
        if bool(prices["symbol"].isna().any()):
            raise ProviderError("prices.csv contains missing symbols")
        if bool(securities["symbol"].isna().any()):
            raise ProviderError("securities.csv contains missing symbols")
        prices["symbol"] = prices["symbol"].astype(str).str.strip().str.upper()
        securities["symbol"] = securities["symbol"].astype(str).str.strip().str.upper()
        if bool((prices["symbol"] == "").any()):
            raise ProviderError("prices.csv contains blank symbols")
        if bool((securities["symbol"] == "").any()):
            raise ProviderError("securities.csv contains blank symbols")

        duplicate_symbols = securities["symbol"][securities["symbol"].duplicated()]
        if not duplicate_symbols.empty:
            raise ProviderError(
                "securities.csv contains duplicate symbols: "
                + ", ".join(sorted(duplicate_symbols.unique())[:5])
            )

        prices["date"] = self._parse_dates(prices["date"], "prices.csv date")
        for column in ("open", "high", "low", "close", "volume", "adj_close"):
            prices[column] = self._to_numeric(prices[column], f"prices.csv {column}")
        prices["dividend_cash"] = self._optional_numeric(prices, "dividend_cash", default=0.0)
        prices["split_factor"] = self._optional_numeric(prices, "split_factor", default=1.0)

        in_range = prices["date"].between(pd.Timestamp(start), pd.Timestamp(end))
        prices = prices.loc[in_range]

        available = set(securities["symbol"])
        if requested is None:
            selected = sorted(available)
        else:
            missing_symbols = sorted(set(requested) - available)
            if missing_symbols:
                raise ProviderError(
                    "requested symbol(s) absent from securities.csv: " + ", ".join(missing_symbols)
                )
            selected = list(requested)
            prices = prices.loc[prices["symbol"].isin(selected)]

        unknown = sorted(set(prices["symbol"]) - available)
        if unknown:
            raise ProviderError(
                "prices.csv contains symbols absent from securities.csv: " + ", ".join(unknown[:5])
            )

        securities = securities.loc[securities["symbol"].isin(selected)].copy()
        securities = self._attach_security_dates(securities, prices)

        counts = prices.groupby("symbol").size()
        no_data = [symbol for symbol in selected if counts.get(symbol, 0) == 0]
        if no_data:
            raise ProviderError("no price bars in range for symbol(s): " + ", ".join(no_data[:10]))

        prices = prices.sort_values(["symbol", "date"]).reset_index(drop=True)
        securities = securities.sort_values("symbol").reset_index(drop=True)

        if "security_id" not in securities.columns:
            securities["security_id"] = pd.NA
        ids = securities["security_id"].astype("string").str.strip()
        securities["security_id"] = ids.mask(ids == "")

        prices = prices.loc[:, list(BAR_COLUMNS)]
        securities = securities.loc[:, list(SECURITY_COLUMNS)]

        filter_note = ",".join(selected)
        payloads = (
            RawPayload(
                filename="securities.csv",
                content=securities_raw,
                request={"format": "csv", "source": str(securities_path), "symbols": filter_note},
            ),
            RawPayload(
                filename="prices.csv",
                content=prices_raw,
                request={
                    "format": "csv",
                    "source": str(prices_path),
                    "symbols": filter_note,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                },
            ),
        )
        return ProviderBatch(
            provider=self.name,
            symbols=tuple(selected),
            start=start,
            end=end,
            bars=prices,
            securities=securities,
            payloads=payloads,
        )

    @staticmethod
    def _read_bytes(path: Path) -> bytes:
        try:
            return path.read_bytes()
        except OSError as exc:
            raise ProviderError(f"cannot read {path}: {exc}") from exc

    @staticmethod
    def _read_csv(raw: bytes, path: Path, *, dtype: dict[str, type[str]]) -> pd.DataFrame:
        try:
            frame = pd.read_csv(io.BytesIO(raw), dtype=dtype)
        except (pd.errors.ParserError, pd.errors.EmptyDataError, UnicodeDecodeError) as exc:
            raise ProviderError(f"cannot parse {path}: {exc}") from exc
        if frame.empty:
            raise ProviderError(f"{path} is empty")
        return frame

    @staticmethod
    def _parse_dates(series: pd.Series, label: str) -> pd.Series:
        parsed = pd.Series(pd.to_datetime(series, errors="coerce", utc=True), name="date")
        parsed = parsed.dt.tz_localize(None).dt.normalize()
        bad = parsed.isna()
        if bool(bad.any()):
            raise ProviderError(f"{label} has {int(bad.sum())} missing or unparseable value(s)")
        return parsed

    @staticmethod
    def _to_numeric(series: pd.Series, label: str) -> pd.Series:
        values = pd.to_numeric(series, errors="coerce").astype(float)
        bad = values.isna()
        if bool(bad.any()):
            raise ProviderError(f"{label} has {int(bad.sum())} missing or non-numeric value(s)")
        return values

    @classmethod
    def _optional_numeric(cls, frame: pd.DataFrame, column: str, *, default: float) -> pd.Series:
        if column not in frame.columns:
            return pd.Series(default, index=frame.index, dtype=float)
        raw = frame[column]
        values = pd.to_numeric(raw, errors="coerce").astype(float)
        bad = values.isna() & raw.notna()
        if bool(bad.any()):
            raise ProviderError(f"prices.csv {column} has non-numeric value(s)")
        return values.fillna(default)

    @staticmethod
    def _attach_security_dates(securities: pd.DataFrame, prices: pd.DataFrame) -> pd.DataFrame:
        first = prices.groupby("symbol")["date"].min()
        last = prices.groupby("symbol")["date"].max()
        for column, derived in (("first_data_date", first), ("last_data_date", last)):
            if column in securities.columns:
                parsed = (
                    pd.Series(
                        pd.to_datetime(securities[column], errors="coerce", utc=True),
                        index=securities.index,
                    )
                    .dt.tz_localize(None)
                    .dt.normalize()
                )
                bad = parsed.isna() & securities[column].notna()
                if bool(bad.any()):
                    raise ProviderError(
                        f"securities.csv {column} has {int(bad.sum())} unparseable value(s)"
                    )
                securities[column] = parsed.fillna(securities["symbol"].map(derived))
            else:
                securities[column] = securities["symbol"].map(derived)
        return securities
