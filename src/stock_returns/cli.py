"""Command-line interface: health check, synthetic data generation, validation."""

from __future__ import annotations

import argparse
import json
import platform
import sys
from pathlib import Path

import pandas as pd

from . import __version__
from .config import Config, SyntheticSettings
from .errors import StockReturnsError
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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="stock-returns",
        description="Research-only stock return platform (Phase 1 foundation).",
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
        raise AssertionError(f"unhandled command: {args.command}")
    except StockReturnsError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    except (OSError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
