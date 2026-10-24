"""Logging setup for command-line entry points."""

from __future__ import annotations

import logging


def configure_logging(level: str = "INFO") -> None:
    """Configure process-wide logging at `level`, replacing existing handlers."""
    logging.basicConfig(
        level=level.upper(),
        format="%(levelname)s %(name)s: %(message)s",
        force=True,
    )
