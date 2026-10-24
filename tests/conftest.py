from pathlib import Path

import pytest

from stock_returns.config import SyntheticSettings
from stock_returns.synthetic import SyntheticDataset, generate_synthetic_dataset

PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = PROJECT_ROOT / "configs" / "default.toml"


@pytest.fixture
def config_path() -> Path:
    return DEFAULT_CONFIG


@pytest.fixture
def synthetic_dataset() -> SyntheticDataset:
    return generate_synthetic_dataset(SyntheticSettings(n_securities=4, n_days=40), seed=11)
