"""Core data contracts.

Two timestamp concepts are kept separate everywhere:

* ``observed_at`` / ``as_of`` — the period the information describes.
* ``available_at`` — the first time the information could have been known.

Point-in-time correctness requires ``available_at >= observed_at``. Forecasts
must only use observations whose ``available_at`` precedes the forecast time.
All datetimes must be timezone-aware; UTC is the project convention.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from datetime import date, datetime

from .errors import ContractError


def _require_text(value: str, field: str) -> None:
    if not isinstance(value, str) or not value.strip():
        raise ContractError(f"{field} must be a non-empty string")


def _require_number(value: float, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ContractError(f"{field} must be a number, got {type(value).__name__}")
    if not math.isfinite(value):
        raise ContractError(f"{field} must be finite, got {value!r}")


def _require_positive_int(value: int, field: str) -> None:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ContractError(f"{field} must be an integer, got {type(value).__name__}")
    if value <= 0:
        raise ContractError(f"{field} must be positive, got {value}")


def _require_aware(value: datetime, field: str) -> None:
    if not isinstance(value, datetime):
        raise ContractError(f"{field} must be a datetime, got {type(value).__name__}")
    if value.tzinfo is None or value.utcoffset() is None:
        raise ContractError(f"{field} must be timezone-aware (UTC recommended), got {value!r}")


def _require_point_in_time(observed_at: datetime, available_at: datetime, *, field: str) -> None:
    _require_aware(observed_at, field)
    _require_aware(available_at, "available_at")
    if available_at < observed_at:
        raise ContractError(f"available_at must be on or after {field} (look-ahead bias)")


@dataclass(frozen=True)
class Security:
    """A listed security identified by a stable id, not by its current ticker."""

    security_id: str
    ticker: str
    name: str
    exchange: str
    first_trade_date: date
    last_trade_date: date | None = None

    def __post_init__(self) -> None:
        for field_name in ("security_id", "ticker", "name", "exchange"):
            _require_text(getattr(self, field_name), field_name)
        if not isinstance(self.first_trade_date, date) or isinstance(
            self.first_trade_date, datetime
        ):
            raise ContractError("first_trade_date must be a date")
        if self.last_trade_date is not None:
            if not isinstance(self.last_trade_date, date) or isinstance(
                self.last_trade_date, datetime
            ):
                raise ContractError("last_trade_date must be a date or None")
            if self.last_trade_date < self.first_trade_date:
                raise ContractError("last_trade_date must not precede first_trade_date")

    def is_listed_on(self, day: date) -> bool:
        """Return whether the security was listed on `day` according to the recorded range."""
        return self.first_trade_date <= day and (
            self.last_trade_date is None or day <= self.last_trade_date
        )


@dataclass(frozen=True)
class Observation:
    """A raw point-in-time value such as a price, macro release, or fundamental."""

    security_id: str
    field: str
    value: float
    observed_at: datetime
    available_at: datetime
    source: str

    def __post_init__(self) -> None:
        _require_text(self.security_id, "security_id")
        _require_text(self.field, "field")
        _require_text(self.source, "source")
        _require_number(self.value, "value")
        _require_point_in_time(self.observed_at, self.available_at, field="observed_at")


@dataclass(frozen=True)
class Feature:
    """A derived model input with explicit point-in-time availability."""

    security_id: str
    name: str
    value: float
    as_of: datetime
    available_at: datetime
    source: str

    def __post_init__(self) -> None:
        _require_text(self.security_id, "security_id")
        _require_text(self.name, "name")
        _require_text(self.source, "source")
        _require_number(self.value, "value")
        _require_point_in_time(self.as_of, self.available_at, field="as_of")


@dataclass(frozen=True)
class Label:
    """A realized forward outcome used for training and evaluation."""

    security_id: str
    as_of: datetime
    horizon_days: int
    forward_return: float
    label_available_at: datetime
    benchmark_return: float | None = None
    excess_return: float | None = None

    def __post_init__(self) -> None:
        _require_text(self.security_id, "security_id")
        _require_aware(self.as_of, "as_of")
        _require_positive_int(self.horizon_days, "horizon_days")
        _require_number(self.forward_return, "forward_return")
        _require_aware(self.label_available_at, "label_available_at")
        if self.label_available_at <= self.as_of:
            raise ContractError(
                "label_available_at must be after as_of (the outcome is not known at forecast time)"
            )
        if self.benchmark_return is not None:
            _require_number(self.benchmark_return, "benchmark_return")
        if self.excess_return is not None:
            _require_number(self.excess_return, "excess_return")


@dataclass(frozen=True)
class Prediction:
    """A model forecast for one security and horizon.

    Probabilities are raw model output until calibration has been evaluated;
    they must not be presented as calibrated without that evidence.
    """

    security_id: str
    as_of: datetime
    horizon_days: int
    expected_return: float
    prob_positive: float
    expected_volatility: float
    model_id: str

    def __post_init__(self) -> None:
        _require_text(self.security_id, "security_id")
        _require_text(self.model_id, "model_id")
        _require_aware(self.as_of, "as_of")
        _require_positive_int(self.horizon_days, "horizon_days")
        _require_number(self.expected_return, "expected_return")
        _require_number(self.prob_positive, "prob_positive")
        if not 0.0 <= self.prob_positive <= 1.0:
            raise ContractError(f"prob_positive must be in [0, 1], got {self.prob_positive}")
        _require_number(self.expected_volatility, "expected_volatility")
        if self.expected_volatility < 0.0:
            raise ContractError(
                f"expected_volatility must be non-negative, got {self.expected_volatility}"
            )


@dataclass(frozen=True)
class ExperimentRecord:
    """Metadata tying a run to its configuration, dataset, and model."""

    experiment_id: str
    created_at: datetime
    config_hash: str
    dataset_id: str
    model_name: str
    notes: str = ""

    def __post_init__(self) -> None:
        _require_text(self.experiment_id, "experiment_id")
        _require_aware(self.created_at, "created_at")
        _require_text(self.config_hash, "config_hash")
        _require_text(self.dataset_id, "dataset_id")
        _require_text(self.model_name, "model_name")
        if not isinstance(self.notes, str):
            raise ContractError("notes must be a string")
