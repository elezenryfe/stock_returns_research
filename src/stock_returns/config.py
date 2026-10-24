"""Typed, validated application configuration loaded from TOML.

Unknown keys are rejected so that typos fail loudly instead of being silently
ignored. Relative paths are interpreted relative to the current working
directory (normally the repository root).
"""

from __future__ import annotations

import math
import tomllib
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Any

from .errors import ConfigError

_DEFAULT_HORIZONS = (21, 42, 63)
_DEFAULT_SEED = 7
_DEFAULT_DATA_DIR = "data"
_DEFAULT_LOG_LEVEL = "INFO"

_TOP_LEVEL_KEYS = frozenset(
    {
        "seed",
        "horizons",
        "data_dir",
        "log_level",
        "validation",
        "synthetic",
        "ingestion",
        "training",
    }
)
_VALIDATION_KEYS = frozenset({"min_price", "max_reported_issues"})
_SYNTHETIC_KEYS = frozenset({"n_securities", "n_days", "start_date"})
_INGESTION_KEYS = frozenset(
    {"raw_dir", "normalized_dir", "max_attempts", "retry_backoff_seconds", "timeout_seconds"}
)
_TRAINING_KEYS = frozenset({"output_dir", "train_fraction"})
_LOG_LEVELS = frozenset({"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"})


@dataclass(frozen=True)
class ValidationSettings:
    min_price: float = 0.01
    max_reported_issues: int = 20


@dataclass(frozen=True)
class SyntheticSettings:
    n_securities: int = 8
    n_days: int = 260
    start_date: str = "2023-01-02"


@dataclass(frozen=True)
class IngestionSettings:
    raw_dir: Path = Path("data/raw")
    normalized_dir: Path = Path("data/normalized")
    max_attempts: int = 3
    retry_backoff_seconds: float = 0.5
    timeout_seconds: float = 30.0


@dataclass(frozen=True)
class TrainingSettings:
    output_dir: Path = Path("data/models")
    train_fraction: float = 0.7


@dataclass(frozen=True)
class Config:
    """Validated application configuration."""

    seed: int = _DEFAULT_SEED
    horizons: tuple[int, ...] = _DEFAULT_HORIZONS
    data_dir: Path = Path(_DEFAULT_DATA_DIR)
    log_level: str = _DEFAULT_LOG_LEVEL
    validation: ValidationSettings = field(default_factory=ValidationSettings)
    synthetic: SyntheticSettings = field(default_factory=SyntheticSettings)
    ingestion: IngestionSettings = field(default_factory=IngestionSettings)
    training: TrainingSettings = field(default_factory=TrainingSettings)
    source_path: Path | None = field(default=None, compare=False)

    @classmethod
    def from_toml(cls, path: str | Path) -> Config:
        """Load and validate the TOML file at `path`."""
        source = Path(path)
        try:
            text = source.read_text(encoding="utf-8")
        except FileNotFoundError as exc:
            raise ConfigError(f"config file not found: {source}") from exc

        try:
            raw = tomllib.loads(text)
        except tomllib.TOMLDecodeError as exc:
            raise ConfigError(f"invalid TOML in {source}: {exc}") from exc

        _reject_unknown(raw, _TOP_LEVEL_KEYS, "top-level")

        seed = _as_int(raw.get("seed", _DEFAULT_SEED), "seed", minimum=0)
        horizons = _as_horizons(raw.get("horizons", list(_DEFAULT_HORIZONS)))
        data_dir = Path(_as_text(raw.get("data_dir", _DEFAULT_DATA_DIR), "data_dir"))
        log_level = _as_text(raw.get("log_level", _DEFAULT_LOG_LEVEL), "log_level").upper()
        if log_level not in _LOG_LEVELS:
            allowed = ", ".join(sorted(_LOG_LEVELS))
            raise ConfigError(f"log_level must be one of [{allowed}], got {log_level!r}")

        validation_raw = _as_table(raw.get("validation", {}), "validation")
        _reject_unknown(validation_raw, _VALIDATION_KEYS, "validation")
        validation = ValidationSettings(
            min_price=_as_float(
                validation_raw.get("min_price", 0.01),
                "validation.min_price",
                minimum_exclusive=0.0,
            ),
            max_reported_issues=_as_int(
                validation_raw.get("max_reported_issues", 20),
                "validation.max_reported_issues",
                minimum=1,
            ),
        )

        synthetic_raw = _as_table(raw.get("synthetic", {}), "synthetic")
        _reject_unknown(synthetic_raw, _SYNTHETIC_KEYS, "synthetic")
        start_date = _as_text(synthetic_raw.get("start_date", "2023-01-02"), "synthetic.start_date")
        try:
            date.fromisoformat(start_date)
        except ValueError as exc:
            raise ConfigError(
                f"synthetic.start_date must be an ISO date (YYYY-MM-DD), got {start_date!r}"
            ) from exc
        synthetic = SyntheticSettings(
            n_securities=_as_int(
                synthetic_raw.get("n_securities", 8), "synthetic.n_securities", minimum=1
            ),
            n_days=_as_int(synthetic_raw.get("n_days", 260), "synthetic.n_days", minimum=2),
            start_date=start_date,
        )

        ingestion_raw = _as_table(raw.get("ingestion", {}), "ingestion")
        _reject_unknown(ingestion_raw, _INGESTION_KEYS, "ingestion")
        ingestion = IngestionSettings(
            raw_dir=Path(
                _as_text(ingestion_raw.get("raw_dir", str(data_dir / "raw")), "ingestion.raw_dir")
            ),
            normalized_dir=Path(
                _as_text(
                    ingestion_raw.get("normalized_dir", str(data_dir / "normalized")),
                    "ingestion.normalized_dir",
                )
            ),
            max_attempts=_as_int(
                ingestion_raw.get("max_attempts", 3), "ingestion.max_attempts", minimum=1
            ),
            retry_backoff_seconds=_as_non_negative_float(
                ingestion_raw.get("retry_backoff_seconds", 0.5),
                "ingestion.retry_backoff_seconds",
            ),
            timeout_seconds=_as_float(
                ingestion_raw.get("timeout_seconds", 30.0),
                "ingestion.timeout_seconds",
                minimum_exclusive=0.0,
            ),
        )

        training_raw = _as_table(raw.get("training", {}), "training")
        _reject_unknown(training_raw, _TRAINING_KEYS, "training")
        training = TrainingSettings(
            output_dir=Path(
                _as_text(
                    training_raw.get("output_dir", str(data_dir / "models")),
                    "training.output_dir",
                )
            ),
            train_fraction=_as_fraction(
                training_raw.get("train_fraction", 0.7), "training.train_fraction"
            ),
        )

        return cls(
            seed=seed,
            horizons=horizons,
            data_dir=data_dir,
            log_level=log_level,
            validation=validation,
            synthetic=synthetic,
            ingestion=ingestion,
            training=training,
            source_path=source,
        )


def _reject_unknown(table: dict[str, Any], allowed: frozenset[str], where: str) -> None:
    unknown = sorted(set(table) - allowed)
    if unknown:
        allowed_list = ", ".join(sorted(allowed))
        raise ConfigError(f"unknown {where} key(s): {', '.join(unknown)} (allowed: {allowed_list})")


def _as_table(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ConfigError(f"{name} must be a TOML table")
    return value


def _as_int(value: object, name: str, *, minimum: int) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ConfigError(f"{name} must be an integer, got {type(value).__name__}")
    if value < minimum:
        raise ConfigError(f"{name} must be >= {minimum}, got {value}")
    return value


def _as_float(value: object, name: str, *, minimum_exclusive: float) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{name} must be a number, got {type(value).__name__}")
    result = float(value)
    if not math.isfinite(result) or result <= minimum_exclusive:
        raise ConfigError(f"{name} must be finite and > {minimum_exclusive}, got {value!r}")
    return result


def _as_non_negative_float(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{name} must be a number, got {type(value).__name__}")
    result = float(value)
    if not math.isfinite(result) or result < 0.0:
        raise ConfigError(f"{name} must be finite and >= 0, got {value!r}")
    return result


def _as_fraction(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ConfigError(f"{name} must be a number, got {type(value).__name__}")
    result = float(value)
    if not math.isfinite(result) or not 0.0 < result < 1.0:
        raise ConfigError(f"{name} must be strictly between 0 and 1, got {value!r}")
    return result


def _as_text(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ConfigError(f"{name} must be a non-empty string")
    return value


def _as_horizons(value: object) -> tuple[int, ...]:
    if not isinstance(value, list) or not value:
        raise ConfigError("horizons must be a non-empty array of positive integers")
    horizons: list[int] = []
    for item in value:
        if isinstance(item, bool) or not isinstance(item, int) or item <= 0:
            raise ConfigError(f"horizons must contain positive integers, got {item!r}")
        horizons.append(item)
    if len(set(horizons)) != len(horizons):
        raise ConfigError("horizons must not contain duplicates")
    return tuple(horizons)
