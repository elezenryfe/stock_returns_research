"""Random-seed convention.

All randomness goes through an explicitly seeded ``numpy`` Generator returned by
`make_rng`. Do not call the legacy global RNGs (`random`, `numpy.random.seed`)
from pipeline code: hidden global state makes experiments irreproducible.
"""

from __future__ import annotations

import numpy as np


def make_rng(seed: int) -> np.random.Generator:
    """Return an isolated random generator for `seed`."""
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise TypeError(f"seed must be an int, got {type(seed).__name__}")
    if seed < 0:
        raise ValueError(f"seed must be non-negative, got {seed}")
    return np.random.default_rng(seed)
