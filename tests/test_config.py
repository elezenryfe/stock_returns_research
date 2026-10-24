from pathlib import Path

import pytest

from stock_returns.config import Config
from stock_returns.errors import ConfigError


def write_config(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def test_project_config_loads(config_path: Path) -> None:
    config = Config.from_toml(config_path)

    assert config.seed == 7
    assert config.horizons == (21, 42, 63)
    assert config.data_dir == Path("data")
    assert config.log_level == "INFO"
    assert config.validation.min_price == 0.01
    assert config.validation.max_reported_issues == 20
    assert config.synthetic.n_securities == 8
    assert config.synthetic.n_days == 260
    assert config.synthetic.start_date == "2023-01-02"
    assert config.ingestion.raw_dir == Path("data/raw")
    assert config.ingestion.normalized_dir == Path("data/normalized")
    assert config.ingestion.max_attempts == 3
    assert config.ingestion.retry_backoff_seconds == 0.5
    assert config.ingestion.timeout_seconds == 30.0
    assert config.source_path == config_path


def test_empty_config_uses_documented_defaults(tmp_path: Path) -> None:
    config = Config.from_toml(write_config(tmp_path, ""))

    assert config.seed == 7
    assert config.horizons == (21, 42, 63)
    assert config.data_dir == Path("data")
    assert config.log_level == "INFO"
    assert config.validation.min_price == 0.01
    assert config.synthetic.n_securities == 8
    assert config.ingestion.raw_dir == Path("data/raw")
    assert config.ingestion.normalized_dir == Path("data/normalized")


def test_ingestion_directories_default_under_data_dir(tmp_path: Path) -> None:
    config = Config.from_toml(write_config(tmp_path, 'data_dir = "custom-data"'))

    assert config.ingestion.raw_dir == Path("custom-data/raw")
    assert config.ingestion.normalized_dir == Path("custom-data/normalized")


def test_missing_config_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="config file not found"):
        Config.from_toml(tmp_path / "absent.toml")


def test_invalid_toml_is_reported(tmp_path: Path) -> None:
    with pytest.raises(ConfigError, match="invalid TOML"):
        Config.from_toml(write_config(tmp_path, "seed = ["))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ("seed = -1", "seed must be >= 0"),
        ("seed = 'seven'", "seed must be an integer"),
        ("seed = true", "seed must be an integer"),
        ("horizons = []", "horizons must be a non-empty array"),
        ("horizons = [21, 0]", "horizons must contain positive integers"),
        ("horizons = [21, 21]", "horizons must not contain duplicates"),
        ("horizons = 21", "horizons must be a non-empty array"),
        ("log_level = 'LOUD'", "log_level must be one of"),
        ("data_dir = ''", "data_dir must be a non-empty string"),
        ("unknown_key = 1", "unknown top-level key"),
        ("validation = 3", "validation must be a TOML table"),
        ("[validation]\nmin_price = 0", "validation.min_price must be finite and >"),
        ("[validation]\nmax_reported_issues = 0", "validation.max_reported_issues must be >="),
        ("[validation]\nnope = 1", "unknown validation key"),
        ("[synthetic]\nn_securities = 0", "synthetic.n_securities must be >="),
        ("[synthetic]\nn_days = 1", "synthetic.n_days must be >="),
        ("[synthetic]\nstart_date = 'not-a-date'", "ISO date"),
        ("[synthetic]\nextra = 1", "unknown synthetic key"),
        ("[ingestion]\nmax_attempts = 0", "ingestion.max_attempts must be >="),
        ("[ingestion]\nretry_backoff_seconds = -1", "must be finite and >= 0"),
        ("[ingestion]\ntimeout_seconds = 0", "must be finite and >"),
        ("[ingestion]\nraw_dir = ''", "ingestion.raw_dir must be a non-empty string"),
        ("[ingestion]\nextra = 1", "unknown ingestion key"),
    ],
)
def test_invalid_values_are_rejected(tmp_path: Path, text: str, message: str) -> None:
    with pytest.raises(ConfigError, match=message):
        Config.from_toml(write_config(tmp_path, text))
