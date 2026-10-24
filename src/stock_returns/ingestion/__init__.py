"""Provider-agnostic market data ingestion."""

from .base import PriceProvider, ProviderBatch, RawPayload
from .coverage import coverage_frame
from .csv_provider import CsvProvider
from .normalize import NORMALIZED_PRICE_COLUMNS, NORMALIZED_SECURITY_COLUMNS
from .pipeline import ingest
from .storage import IngestResult, read_normalized
from .tiingo import TiingoProvider

__all__ = [
    "CsvProvider",
    "IngestResult",
    "NORMALIZED_PRICE_COLUMNS",
    "NORMALIZED_SECURITY_COLUMNS",
    "PriceProvider",
    "ProviderBatch",
    "RawPayload",
    "TiingoProvider",
    "coverage_frame",
    "ingest",
    "read_normalized",
]
