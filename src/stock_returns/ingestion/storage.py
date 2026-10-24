"""Provenance-preserving raw snapshots and atomic normalized storage.

Layout::

    <raw_dir>/<provider>/<run_id>/manifest.json
    <raw_dir>/<provider>/<run_id>/payloads/<file>
    <normalized_dir>/prices.parquet
    <normalized_dir>/securities.parquet
    <normalized_dir>/manifest.json

Raw snapshots are immutable by convention and kept per run. The normalized
files hold the latest revision per ``(security_id, date)`` so re-running an
ingestion does not duplicate rows. Run ids are timestamp + request hash.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path

import pandas as pd
import pyarrow as pa

from ..errors import IngestionError, ProviderError
from .base import ProviderBatch
from .normalize import NORMALIZED_PRICE_COLUMNS, NORMALIZED_SECURITY_COLUMNS

logger = logging.getLogger(__name__)

RAW_SCHEMA_VERSION = 1
NORMALIZED_SCHEMA_VERSION = 1
PRICES_FILENAME = "prices.parquet"
SECURITIES_FILENAME = "securities.parquet"
MANIFEST_FILENAME = "manifest.json"


@dataclass(frozen=True)
class IngestResult:
    run_id: str
    provider: str
    raw_dir: Path
    prices_path: Path
    securities_path: Path
    securities_count: int
    rows_received: int
    rows_new: int
    rows_total: int


def make_run_id(
    provider: str,
    symbols: tuple[str, ...] | list[str],
    start: date,
    end: date,
    retrieved_at: datetime,
) -> str:
    request = json.dumps(
        {
            "provider": provider,
            "symbols": sorted(symbols),
            "start": start.isoformat(),
            "end": end.isoformat(),
        },
        sort_keys=True,
    )
    digest = hashlib.sha256(request.encode("utf-8")).hexdigest()[:8]
    timestamp = retrieved_at.astimezone(UTC).strftime("%Y%m%dT%H%M%SZ")
    return f"{timestamp}-{digest}"


def write_raw_snapshot(
    batch: ProviderBatch, *, run_id: str, raw_dir: Path, retrieved_at: datetime
) -> Path:
    """Write raw provider payloads and a manifest; return the run directory."""
    run_dir = raw_dir / batch.provider / run_id
    payload_dir = run_dir / "payloads"
    payload_dir.mkdir(parents=True, exist_ok=True)

    manifest_payloads: list[dict[str, object]] = []
    for payload in batch.payloads:
        filename = Path(payload.filename).name
        if not filename or filename != payload.filename:
            raise ProviderError(f"unsafe raw payload filename: {payload.filename!r}")
        target = payload_dir / filename
        target.write_bytes(payload.content)
        manifest_payloads.append(
            {
                "filename": f"payloads/{filename}",
                "sha256": hashlib.sha256(payload.content).hexdigest(),
                "bytes": len(payload.content),
                "request": payload.request,
            }
        )

    manifest = {
        "schema_version": RAW_SCHEMA_VERSION,
        "run_id": run_id,
        "provider": batch.provider,
        "requested_symbols": list(batch.symbols),
        "start_date": batch.start.isoformat(),
        "end_date": batch.end.isoformat(),
        "retrieved_at": retrieved_at.astimezone(UTC).isoformat(),
        "rows": len(batch.bars),
        "payloads": manifest_payloads,
    }
    _write_json_atomic(run_dir / MANIFEST_FILENAME, manifest)
    return run_dir


def load_normalized_if_exists(
    normalized_dir: Path,
) -> tuple[pd.DataFrame | None, pd.DataFrame | None]:
    prices_path = normalized_dir / PRICES_FILENAME
    securities_path = normalized_dir / SECURITIES_FILENAME
    if not prices_path.exists() and not securities_path.exists():
        return None, None
    if not prices_path.exists() or not securities_path.exists():
        raise IngestionError(
            f"incomplete normalized dataset in {normalized_dir}: both "
            f"{PRICES_FILENAME} and {SECURITIES_FILENAME} are required"
        )
    try:
        prices = pd.read_parquet(prices_path)
        securities = pd.read_parquet(securities_path)
    except (OSError, pa.ArrowException) as exc:
        raise IngestionError(f"cannot read normalized dataset in {normalized_dir}: {exc}") from exc
    return prices, securities


def read_normalized(normalized_dir: Path) -> tuple[pd.DataFrame, pd.DataFrame]:
    prices, securities = load_normalized_if_exists(normalized_dir)
    if prices is None or securities is None:
        raise IngestionError(
            f"no normalized dataset in {normalized_dir}; run `stock-returns ingest` first"
        )
    return prices, securities


def merge_normalized(
    existing_prices: pd.DataFrame | None,
    new_prices: pd.DataFrame,
    existing_securities: pd.DataFrame | None,
    new_securities: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, int]:
    """Return merged frames and the number of newly added price rows."""
    if existing_prices is not None and not existing_prices.empty:
        missing = [
            column for column in NORMALIZED_PRICE_COLUMNS if column not in existing_prices.columns
        ]
        if missing:
            raise IngestionError(
                "existing normalized prices missing column(s): " + ", ".join(missing)
            )
    if existing_securities is not None and not existing_securities.empty:
        missing = [
            column
            for column in NORMALIZED_SECURITY_COLUMNS
            if column not in existing_securities.columns
        ]
        if missing:
            raise IngestionError(
                "existing normalized securities missing column(s): " + ", ".join(missing)
            )

    if existing_prices is None or existing_prices.empty:
        prices = new_prices.copy()
        rows_new = len(prices)
    else:
        previous_rows = len(existing_prices)
        prices = pd.concat([existing_prices, new_prices], ignore_index=True)
        prices = prices.drop_duplicates(subset=["security_id", "date"], keep="last")
        rows_new = max(0, len(prices) - previous_rows)

    prices = prices.sort_values(["security_id", "date"]).reset_index(drop=True)
    prices = prices.loc[:, list(NORMALIZED_PRICE_COLUMNS)]

    if existing_securities is None or existing_securities.empty:
        securities = new_securities.copy()
    else:
        securities = pd.concat([existing_securities, new_securities], ignore_index=True)
        securities = securities.drop_duplicates(subset=["security_id"], keep="last")
    securities = securities.sort_values("security_id").reset_index(drop=True)
    securities = securities.loc[:, list(NORMALIZED_SECURITY_COLUMNS)]
    return prices, securities, rows_new


def write_normalized(
    prices: pd.DataFrame,
    securities: pd.DataFrame,
    *,
    normalized_dir: Path,
    run_id: str,
    ingested_at: datetime,
) -> tuple[Path, Path]:
    """Atomically replace normalized files and write their manifest.

    shortcut: one writer at a time; there is no cross-process lock. Add file
    locking before concurrent ingestion is supported.
    """
    normalized_dir.mkdir(parents=True, exist_ok=True)
    prices_path = normalized_dir / PRICES_FILENAME
    securities_path = normalized_dir / SECURITIES_FILENAME
    _write_parquet_atomic(prices, prices_path)
    _write_parquet_atomic(securities, securities_path)

    manifest = {
        "schema_version": NORMALIZED_SCHEMA_VERSION,
        "last_run_id": run_id,
        "updated_at": ingested_at.astimezone(UTC).isoformat(),
        "rows": len(prices),
        "securities": len(securities),
        "files": {
            PRICES_FILENAME: _sha256_file(prices_path),
            SECURITIES_FILENAME: _sha256_file(securities_path),
        },
    }
    _write_json_atomic(normalized_dir / MANIFEST_FILENAME, manifest)
    return prices_path, securities_path


def _write_parquet_atomic(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        frame.to_parquet(temp, index=False)
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _write_json_atomic(path: Path, payload: dict[str, object]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_name(path.name + ".tmp")
    try:
        temp.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        os.replace(temp, path)
    finally:
        if temp.exists():
            temp.unlink()


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()
