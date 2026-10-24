import json
from pathlib import Path

import pandas as pd
import pytest

from stock_returns.cli import main


def write_config(tmp_path: Path, data_dir: Path) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(f'data_dir = "{data_dir.as_posix()}"\n', encoding="utf-8")
    return path


def test_health_reports_ok(config_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--config", str(config_path), "health"])

    captured = capsys.readouterr()
    payload = json.loads(captured.out)
    assert exit_code == 0
    assert payload["status"] == "ok"
    assert payload["seed"] == 7
    assert payload["horizons"] == [21, 42, 63]
    assert captured.err == ""


def test_generate_then_validate_with_default_paths(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = write_config(tmp_path, tmp_path / "data")

    generate_code = main(
        [
            "--config",
            str(config_path),
            "generate-synthetic",
            "--securities",
            "3",
            "--days",
            "12",
            "--seed",
            "5",
        ]
    )
    generate_out = capsys.readouterr().out
    assert generate_code == 0
    assert "wrote 3 securities and 36 price rows" in generate_out
    assert (tmp_path / "data" / "synthetic" / "prices.csv").is_file()
    assert (tmp_path / "data" / "synthetic" / "securities.csv").is_file()

    validate_code = main(["--config", str(config_path), "validate"])
    validate_out = capsys.readouterr().out
    assert validate_code == 0
    assert validate_out.startswith("ok: 3 securities, 36 price rows,")


def test_invalid_data_exits_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = write_config(tmp_path, tmp_path / "data")
    assert (
        main(
            [
                "--config",
                str(config_path),
                "generate-synthetic",
                "--securities",
                "2",
                "--days",
                "5",
            ]
        )
        == 0
    )
    capsys.readouterr()

    prices_path = tmp_path / "data" / "synthetic" / "prices.csv"
    prices = pd.read_csv(prices_path)
    prices.loc[0, "close"] = -5.0
    prices.to_csv(prices_path, index=False)

    exit_code = main(["--config", str(config_path), "validate"])
    captured = capsys.readouterr()
    assert exit_code == 1
    assert captured.out == ""
    assert "error:" in captured.err
    assert "below min_price" in captured.err


def test_missing_config_exits_nonzero(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    exit_code = main(["--config", str(tmp_path / "absent.toml"), "health"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "config file not found" in captured.err


def test_argparse_rejects_nonpositive_days(config_path: Path) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--config", str(config_path), "generate-synthetic", "--days", "0"])

    assert exc_info.value.code == 2


def test_ingest_csv_then_coverage(
    tmp_path: Path, csv_source_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config_path = write_config(tmp_path, tmp_path / "data")
    ingest_args = [
        "--config",
        str(config_path),
        "ingest",
        "--provider",
        "csv",
        "--input",
        str(csv_source_dir),
        "--start",
        "2023-01-02",
        "--end",
        "2023-12-31",
    ]

    assert main(ingest_args) == 0
    output = capsys.readouterr().out
    assert "ingested csv run" in output
    assert "36 row(s) received, 36 new, 36 total, 3 securities" in output

    assert main(ingest_args) == 0
    output = capsys.readouterr().out
    assert "36 row(s) received, 0 new, 36 total" in output

    assert main(["--config", str(config_path), "coverage", "--limit", "2"]) == 0
    output = capsys.readouterr().out
    assert output.startswith("coverage: 3 securities, 36 price rows,")
    assert "... 1 more (use --limit)" in output


def test_ingest_csv_requires_input(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = write_config(tmp_path, tmp_path / "data")

    exit_code = main(
        [
            "--config",
            str(config_path),
            "ingest",
            "--provider",
            "csv",
            "--start",
            "2023-01-02",
            "--end",
            "2023-12-31",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "--input is required" in captured.err


def test_ingest_tiingo_requires_api_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.delenv("TIINGO_API_KEY", raising=False)
    config_path = write_config(tmp_path, tmp_path / "data")

    exit_code = main(
        [
            "--config",
            str(config_path),
            "ingest",
            "--provider",
            "tiingo",
            "--symbols",
            "AAPL",
            "--start",
            "2023-01-02",
            "--end",
            "2023-12-31",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "TIINGO_API_KEY" in captured.err


def test_coverage_without_ingested_data(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    config_path = write_config(tmp_path, tmp_path / "data")

    exit_code = main(["--config", str(config_path), "coverage"])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "no normalized dataset" in captured.err
