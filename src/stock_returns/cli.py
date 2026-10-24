"""Command-line interface: health, synthetic data, ingestion, validation, coverage."""

from __future__ import annotations

import argparse
import json
import os
import platform
import sys
from datetime import date
from pathlib import Path

import pandas as pd

from . import __version__
from .config import Config, SyntheticSettings
from .errors import IngestionError, StockReturnsError
from .ingestion import CsvProvider, TiingoProvider, coverage_frame, ingest, read_normalized
from .logging_utils import configure_logging
from .synthetic import generate_synthetic_dataset, write_dataset
from .validation import validate_dataset

DEFAULT_CONFIG_PATH = Path("configs/default.toml")


def _positive_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an integer, got {text!r}") from exc
    if value <= 0:
        raise argparse.ArgumentTypeError(f"must be positive, got {value}")
    return value


def _non_negative_int(text: str) -> int:
    try:
        value = int(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(f"expected an integer, got {text!r}") from exc
    if value < 0:
        raise argparse.ArgumentTypeError(f"must be non-negative, got {value}")
    return value


def _iso_date(text: str) -> date:
    try:
        return date.fromisoformat(text)
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            f"expected an ISO date (YYYY-MM-DD), got {text!r}"
        ) from exc


def _symbol_list(text: str) -> tuple[str, ...]:
    symbols = tuple(dict.fromkeys(part.strip().upper() for part in text.split(",") if part.strip()))
    if not symbols:
        raise argparse.ArgumentTypeError("at least one symbol is required")
    return symbols


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock-returns",
        description="Research-only stock return platform (Phase 2 ingestion).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=DEFAULT_CONFIG_PATH,
        help=f"TOML configuration file (default: {DEFAULT_CONFIG_PATH})",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    subparsers.add_parser("health", help="check the installation and configuration")

    generate = subparsers.add_parser(
        "generate-synthetic", help="write a deterministic synthetic dataset"
    )
    generate.add_argument(
        "--out", type=Path, help="output directory (default: <data_dir>/synthetic)"
    )
    generate.add_argument("--securities", type=_positive_int, help="number of securities")
    generate.add_argument("--days", type=_positive_int, help="number of business days")
    generate.add_argument("--seed", type=_non_negative_int, help="random seed override")

    validate = subparsers.add_parser("validate", help="validate a price/security dataset")
    validate.add_argument("--prices", type=Path, help="prices CSV (default: <data_dir>/synthetic)")
    validate.add_argument(
        "--securities", type=Path, help="securities CSV (default: <data_dir>/synthetic)"
    )

    ingest_parser = subparsers.add_parser("ingest", help="fetch and store prices from a provider")
    ingest_parser.add_argument("--provider", choices=("csv", "tiingo"), required=True)
    ingest_parser.add_argument("--input", type=Path, help="input directory for the csv provider")
    ingest_parser.add_argument(
        "--symbols", type=_symbol_list, help="comma-separated tickers (required for tiingo)"
    )
    ingest_parser.add_argument("--start", type=_iso_date, required=True)
    ingest_parser.add_argument("--end", type=_iso_date, required=True)

    coverage = subparsers.add_parser(
        "coverage", help="summarize the normalized dataset's date coverage"
    )
    coverage.add_argument(
        "--limit", type=_positive_int, default=25, help="securities to list (default: 25)"
    )
    return parser


def _health(config: Config) -> int:
    payload = {
        "status": "ok",
        "version": __version__,
        "python": platform.python_version(),
        "pandas": pd.__version__,
        "config": str(config.source_path),
        "seed": config.seed,
        "horizons": list(config.horizons),
        "data_dir": str(config.data_dir),
    }
    print(json.dumps(payload, indent=2))
    return 0


def _generate(config: Config, args: argparse.Namespace) -> int:
    settings = SyntheticSettings(
        n_securities=(
            args.securities if args.securities is not None else config.synthetic.n_securities
        ),
        n_days=args.days if args.days is not None else config.synthetic.n_days,
        start_date=config.synthetic.start_date,
    )
    seed = config.seed if args.seed is None else args.seed
    out_dir = args.out if args.out is not None else config.data_dir / "synthetic"

    dataset = generate_synthetic_dataset(settings, seed=seed)
    prices_path, securities_path = write_dataset(dataset, out_dir)

    print(f"wrote {len(dataset.securities)} securities and {len(dataset.prices)} price rows")
    print(f"  {securities_path}")
    print(f"  {prices_path}")
    return 0


def _validate(config: Config, args: argparse.Namespace) -> int:
    default_dir = config.data_dir / "synthetic"
    prices_path = args.prices if args.prices is not None else default_dir / "prices.csv"
    securities_path = (
        args.securities if args.securities is not None else default_dir / "securities.csv"
    )

    prices = pd.read_csv(prices_path)
    securities = pd.read_csv(securities_path)
    validated_prices, validated_securities = validate_dataset(
        prices,
        securities,
        min_price=config.validation.min_price,
        max_issues=config.validation.max_reported_issues,
    )

    first_day = validated_prices["date"].min()
    last_day = validated_prices["date"].max()
    print(
        f"ok: {len(validated_securities)} securities, {len(validated_prices)} price rows, "
        f"{first_day:%Y-%m-%d}..{last_day:%Y-%m-%d}"
    )
    return 0


def _ingest(config: Config, args: argparse.Namespace) -> int:
    if args.start > args.end:
        raise IngestionError("--start must not be after --end")

    if args.provider == "csv":
        if args.input is None:
            raise IngestionError("--input is required for the csv provider")
        provider: CsvProvider | TiingoProvider = CsvProvider(args.input)
    elif args.provider == "tiingo":
        if args.symbols is None:
            raise IngestionError("--symbols is required for the tiingo provider")
        provider = TiingoProvider(
            os.environ.get("TIINGO_API_KEY", ""),
            max_attempts=config.ingestion.max_attempts,
            retry_backoff_seconds=config.ingestion.retry_backoff_seconds,
            timeout_seconds=config.ingestion.timeout_seconds,
        )
    else:
        raise AssertionError(f"unhandled provider: {args.provider}")

    result = ingest(
        provider,
        config=config,
        symbols=args.symbols,
        start=args.start,
        end=args.end,
    )
    print(
        f"ingested {result.provider} run {result.run_id}: "
        f"{result.rows_received} row(s) received, {result.rows_new} new, "
        f"{result.rows_total} total, {result.securities_count} securities"
    )
    print(f"  raw: {result.raw_dir}")
    print(f"  normalized: {result.prices_path.parent}")
    return 0


def _coverage(config: Config, args: argparse.Namespace) -> int:
    prices, _ = read_normalized(config.ingestion.normalized_dir)
    report = coverage_frame(prices)
    first_day = report["first_date"].min()
    last_day = report["last_date"].max()
    print(
        f"coverage: {len(report)} securities, {len(prices)} price rows, "
        f"{first_day:%Y-%m-%d}..{last_day:%Y-%m-%d}"
    )
    for row in report.head(args.limit).itertuples(index=False):
        print(
            f"  {row.security_id:<24} {row.symbol:<12} {row.rows:>6} rows  "
            f"{row.first_date:%Y-%m-%d}..{row.last_date:%Y-%m-%d}  "
            f"missing_weekdays={row.missing_weekdays}"
        )
    if len(report) > args.limit:
        print(f"  ... {len(report) - args.limit} more (use --limit)")
    return 0


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        config = Config.from_toml(args.config)
        configure_logging(config.log_level)
        if args.command == "health":
            return _health(config)
        if args.command == "generate-synthetic":
            return _generate(config, args)
        if args.command == "validate":
            return _validate(config, args)
        if args.command == "ingest":
            return _ingest(config, args)
        if args.command == "coverage":
            return _coverage(config, args)
        raise AssertionError(f"unhandled command: {args.command}")
    except StockReturnsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
