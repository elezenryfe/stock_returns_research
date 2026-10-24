"""Ingestion orchestration: fetch, snapshot, merge, validate, commit."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from datetime import UTC, date, datetime

from ..config import Config
from ..errors import IngestionError
from .base import PriceProvider, normalize_symbols
from .normalize import normalize_batch, validate_normalized_dataset
from .storage import (
    IngestResult,
    load_normalized_if_exists,
    make_run_id,
    merge_normalized,
    write_normalized,
    write_raw_snapshot,
)

logger = logging.getLogger(__name__)


def ingest(
    provider: PriceProvider,
    *,
    config: Config,
    symbols: Sequence[str] | None,
    start: date,
    end: date,
    now: Callable[[], datetime] | None = None,
) -> IngestResult:
    """Fetch one provider request and commit it idempotently.

    Nothing in the normalized dataset changes unless the complete merged
    dataset validates. Raw payloads are retained even when later validation
    fails, which makes failures reproducible.
    """
    if start > end:
        raise IngestionError("start date must not be after end date")
    retrieved_at = (now or (lambda: datetime.now(UTC)))()
    if retrieved_at.tzinfo is None or retrieved_at.utcoffset() is None:
        raise IngestionError("retrieval timestamp must be timezone-aware")
    retrieved_at = retrieved_at.astimezone(UTC)

    batch = provider.fetch(symbols, start, end)
    if batch.provider != provider.name:
        raise IngestionError(
            f"provider returned batch name {batch.provider!r}, expected {provider.name!r}"
        )
    if batch.start != start or batch.end != end:
        raise IngestionError("provider batch date range does not match the request")
    if symbols is not None:
        requested = normalize_symbols(symbols)
        if requested is None or set(batch.symbols) != set(requested):
            missing = sorted(set(requested or ()) - set(batch.symbols))
            extra = sorted(set(batch.symbols) - set(requested or ()))
            detail = []
            if missing:
                detail.append(f"missing: {', '.join(missing)}")
            if extra:
                detail.append(f"unexpected: {', '.join(extra)}")
            raise IngestionError(
                "provider symbol set differs from request (" + "; ".join(detail) + ")"
            )

    run_id = make_run_id(batch.provider, batch.symbols, start, end, retrieved_at)
    logger.info(
        "ingesting %d symbol(s) from %s over %s..%s (run %s)",
        len(batch.symbols),
        batch.provider,
        start,
        end,
        run_id,
    )

    new_prices, new_securities = normalize_batch(batch, run_id=run_id, ingested_at=retrieved_at)
    run_dir = write_raw_snapshot(
        batch, run_id=run_id, raw_dir=config.ingestion.raw_dir, retrieved_at=retrieved_at
    )

    existing_prices, existing_securities = load_normalized_if_exists(
        config.ingestion.normalized_dir
    )
    prices, securities, rows_new = merge_normalized(
        existing_prices, new_prices, existing_securities, new_securities
    )
    prices, securities = validate_normalized_dataset(
        prices,
        securities,
        min_price=config.validation.min_price,
        max_issues=config.validation.max_reported_issues,
    )
    prices_path, securities_path = write_normalized(
        prices,
        securities,
        normalized_dir=config.ingestion.normalized_dir,
        run_id=run_id,
        ingested_at=retrieved_at,
    )

    logger.info(
        "committed run %s: %d new row(s), %d total row(s), %d securities",
        run_id,
        rows_new,
        len(prices),
        len(securities),
    )
    return IngestResult(
        run_id=run_id,
        provider=batch.provider,
        raw_dir=run_dir,
        prices_path=prices_path,
        securities_path=securities_path,
        securities_count=len(securities),
        rows_received=len(new_prices),
        rows_new=rows_new,
        rows_total=len(prices),
    )
