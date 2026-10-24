"""User-facing exception hierarchy.

The CLI catches `StockReturnsError` and prints a readable message instead of a
traceback. Unexpected programming errors are deliberately not caught.
"""

from __future__ import annotations


class StockReturnsError(Exception):
    """Base class for expected, user-facing failures."""


class ConfigError(StockReturnsError):
    """The configuration file is missing, malformed, or invalid."""


class ContractError(StockReturnsError, ValueError):
    """A core data contract was violated."""


class DataValidationError(StockReturnsError):
    """A dataset failed schema or data-quality validation."""

    def __init__(self, issues: list[str]) -> None:
        self.issues = list(issues)
        detail = "\n".join(f"  - {issue}" for issue in self.issues)
        super().__init__(f"{len(self.issues)} validation issue(s):\n{detail}")


class IngestionError(StockReturnsError):
    """Data ingestion failed before a normalized dataset was committed."""


class ProviderError(IngestionError):
    """A data provider is unavailable, misconfigured, or returned invalid payloads."""


class TrainingError(StockReturnsError):
    """Feature/label construction or model training failed on invalid input."""
