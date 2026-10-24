from datetime import UTC

import pandas as pd
import pytest

from stock_returns.errors import TrainingError
from stock_returns.features import prepare_panel
from stock_returns.labels import compute_labels, market_close_timestamp


def make_frame(n_days: int = 12, n_securities: int = 3) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    rows = []
    for security_index in range(n_securities):
        for day_index, day in enumerate(dates):
            close = 100.0 + 10.0 * security_index + 2.0 * day_index
            adjusted = close * (1.0 + 0.01 * day_index * (security_index + 1))
            rows.append(
                {
                    "security_id": f"SEC{security_index + 1}",
                    "date": day,
                    "close": close,
                    "adj_close": adjusted,
                    "volume": 1_000.0,
                }
            )
    return pd.DataFrame(rows)


def adjusted_series(frame: pd.DataFrame, security_id: str) -> pd.Series:
    return frame.loc[frame["security_id"] == security_id, "adj_close"].reset_index(drop=True)


def test_forward_return_uses_adjusted_close() -> None:
    frame = make_frame()
    labels = compute_labels(prepare_panel(frame), [2])
    day = pd.Timestamp("2024-01-01")
    adjusted = adjusted_series(frame, "SEC1")

    row = labels.loc[(labels["security_id"] == "SEC1") & (labels["date"] == day)]
    assert len(row) == 1
    assert row.iloc[0]["forward_return"] == pytest.approx(adjusted.iloc[2] / adjusted.iloc[0] - 1)


def test_benchmark_is_equal_weight_mean_and_excess_is_the_difference() -> None:
    frame = make_frame()
    labels = compute_labels(prepare_panel(frame), [2])
    day = pd.Timestamp("2024-01-01")

    at_day = labels.loc[labels["date"] == day]
    benchmark = at_day["forward_return"].mean()
    assert len(at_day) == 3
    assert at_day["benchmark_return"].tolist() == pytest.approx([benchmark] * 3)
    assert at_day["excess_return"].tolist() == pytest.approx(
        (at_day["forward_return"] - benchmark).tolist()
    )


def test_label_timestamps_follow_the_trading_calendar() -> None:
    frame = make_frame()
    dates = pd.bdate_range("2024-01-01", periods=12)
    labels = compute_labels(prepare_panel(frame), [2])
    at_first = labels.loc[labels["date"] == dates[0]].iloc[0]

    assert at_first["as_of"] == market_close_timestamp(dates[0])
    assert at_first["as_of"].tzinfo == UTC
    assert at_first["label_available_at"] == market_close_timestamp(dates[2])
    assert at_first["label_available_at"] > at_first["as_of"]


def test_last_horizon_dates_have_no_labels() -> None:
    frame = make_frame()
    dates = pd.bdate_range("2024-01-01", periods=12)
    labels = compute_labels(prepare_panel(frame), [2])

    assert labels["date"].max() == dates[-1 - 2]
    assert not dates[-2:].isin(labels["date"].unique()).any()


def test_missing_forward_bar_removes_the_label_and_benchmark_member() -> None:
    frame = make_frame()
    dates = pd.bdate_range("2024-01-01", periods=12)
    frame = frame.drop(
        frame.loc[(frame["security_id"] == "SEC2") & (frame["date"] == dates[2])].index
    )

    labels = compute_labels(prepare_panel(frame), [2])
    at_first = labels.loc[labels["date"] == dates[0]]

    assert not (at_first["security_id"] == "SEC2").any()
    expected_benchmark = (
        adjusted_series(frame, "SEC1").iloc[2] / adjusted_series(frame, "SEC1").iloc[0]
        - 1
        + adjusted_series(frame, "SEC3").iloc[2] / adjusted_series(frame, "SEC3").iloc[0]
        - 1
    ) / 2
    assert at_first["benchmark_return"].iloc[0] == pytest.approx(expected_benchmark)


def test_missing_start_bar_removes_the_label() -> None:
    frame = make_frame()
    dates = pd.bdate_range("2024-01-01", periods=12)
    frame = frame.drop(
        frame.loc[(frame["security_id"] == "SEC2") & (frame["date"] == dates[0])].index
    )

    labels = compute_labels(prepare_panel(frame), [2])

    assert not ((labels["security_id"] == "SEC2") & (labels["date"] == dates[0])).any()
    assert labels.loc[labels["date"] == dates[0], "security_id"].tolist() == [
        "SEC1",
        "SEC3",
    ]


def test_future_prices_change_labels() -> None:
    frame = make_frame()
    dates = pd.bdate_range("2024-01-01", periods=12)
    baseline = compute_labels(prepare_panel(frame), [2])
    mutated = frame.copy()
    mutated.loc[
        (mutated["security_id"] == "SEC1") & (mutated["date"] == dates[2]), "adj_close"
    ] *= 2.0
    changed = compute_labels(prepare_panel(mutated), [2])

    baseline_value = baseline.loc[
        (baseline["security_id"] == "SEC1") & (baseline["date"] == dates[0]),
        "forward_return",
    ].iloc[0]
    changed_value = changed.loc[
        (changed["security_id"] == "SEC1") & (changed["date"] == dates[0]),
        "forward_return",
    ].iloc[0]
    assert changed_value != pytest.approx(baseline_value)


def test_horizon_must_fit_the_calendar() -> None:
    frame = make_frame(n_days=5)

    with pytest.raises(TrainingError, match="requires more than"):
        compute_labels(prepare_panel(frame), [5])


def test_duplicate_and_invalid_horizons_are_rejected() -> None:
    frame = make_frame()
    panel = prepare_panel(frame)

    with pytest.raises(TrainingError, match="duplicates"):
        compute_labels(panel, [2, 2])
    with pytest.raises(TrainingError, match="positive integers"):
        compute_labels(panel, [0])
