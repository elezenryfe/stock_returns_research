import numpy as np
import pandas as pd
import pytest

from stock_returns.errors import TrainingError
from stock_returns.features import (
    FEATURE_COLUMNS,
    compute_features,
    prepare_panel,
)


def make_frame(n_days: int = 80, n_securities: int = 2) -> pd.DataFrame:
    dates = pd.bdate_range("2024-01-01", periods=n_days)
    rows = []
    for security_index in range(n_securities):
        for day_index, day in enumerate(dates):
            close = 100.0 + 5.0 * day_index + 10.0 * security_index
            rows.append(
                {
                    "security_id": f"SEC{security_index + 1}",
                    "date": day,
                    "close": close,
                    "adj_close": close * (1.0 + 0.002 * day_index + 0.01 * security_index),
                    "volume": 1_000.0 + 10.0 * day_index + 100.0 * security_index,
                }
            )
    return pd.DataFrame(rows)


def row_at(features: pd.DataFrame, security_id: str, day: pd.Timestamp) -> pd.Series:
    match = features.loc[(features["security_id"] == security_id) & (features["date"] == day)]
    assert len(match) == 1
    return match.iloc[0]


def test_adjusted_close_is_preferred_and_close_is_the_fallback() -> None:
    frame = make_frame()

    assert prepare_panel(frame).return_price_column == "adj_close"
    assert prepare_panel(frame.drop(columns=["adj_close"])).return_price_column == "close"


def test_close_is_required_for_dollar_volume() -> None:
    with pytest.raises(TrainingError, match="close"):
        prepare_panel(make_frame().drop(columns=["close"]))


def test_duplicate_security_dates_are_rejected() -> None:
    frame = make_frame()

    with pytest.raises(TrainingError, match="duplicate"):
        prepare_panel(pd.concat([frame, frame.iloc[[0]]], ignore_index=True))


def test_nonpositive_price_is_rejected() -> None:
    frame = make_frame()
    frame.loc[0, "adj_close"] = 0.0

    with pytest.raises(TrainingError, match="non-positive"):
        prepare_panel(frame)


def test_unparseable_date_is_rejected() -> None:
    frame = make_frame()
    frame["date"] = frame["date"].astype(str)
    frame.loc[0, "date"] = "not-a-date"

    with pytest.raises(TrainingError, match="unparseable"):
        prepare_panel(frame)


def test_feature_frame_retains_warmup_rows() -> None:
    frame = make_frame()

    features = compute_features(prepare_panel(frame))

    assert list(features.columns) == ["security_id", "date", *FEATURE_COLUMNS]
    assert len(features) == len(frame)
    assert pd.isna(row_at(features, "SEC1", pd.Timestamp("2024-01-01"))["momentum_5"])


@pytest.mark.parametrize(
    ("column", "window"),
    [("momentum_5", 5), ("momentum_21", 21), ("momentum_63", 63)],
)
def test_momentum_matches_manual_price_ratio(column: str, window: int) -> None:
    frame = make_frame()
    features = compute_features(prepare_panel(frame))
    day = pd.Timestamp("2024-04-10")
    position = int(
        frame.loc[frame["security_id"] == "SEC1", "date"].reset_index(drop=True).tolist().index(day)
    )
    prices = frame.loc[frame["security_id"] == "SEC1", "adj_close"].reset_index(drop=True)

    expected = prices.iloc[position] / prices.iloc[position - window] - 1.0
    assert row_at(features, "SEC1", day)[column] == pytest.approx(expected)


def test_volatility_matches_manual_sample_std() -> None:
    frame = make_frame()
    features = compute_features(prepare_panel(frame))
    day = pd.Timestamp("2024-04-10")
    prices = frame.loc[frame["security_id"] == "SEC1", "adj_close"].reset_index(drop=True)
    position = int(
        frame.loc[frame["security_id"] == "SEC1", "date"].reset_index(drop=True).tolist().index(day)
    )
    returns = prices.pct_change(fill_method=None)

    expected = returns.iloc[position - 20 : position + 1].std()
    assert row_at(features, "SEC1", day)["volatility_21"] == pytest.approx(expected)


def test_dollar_volume_matches_manual_mean() -> None:
    frame = make_frame()
    features = compute_features(prepare_panel(frame))
    day = pd.Timestamp("2024-04-10")
    sec = frame.loc[frame["security_id"] == "SEC1"].reset_index(drop=True)
    position = int(sec["date"].tolist().index(day))
    dollar_volume = sec["close"] * sec["volume"]

    expected = np.log1p(dollar_volume.iloc[position - 20 : position + 1].mean())
    assert row_at(features, "SEC1", day)["log_dollar_volume_21"] == pytest.approx(expected)


def test_market_relative_momentum_is_cross_sectionally_centered() -> None:
    features = compute_features(prepare_panel(make_frame(n_securities=3)))
    day = pd.Timestamp("2024-04-10")

    values = features.loc[features["date"] == day, "market_relative_momentum_21"]

    assert len(values) == 3
    assert values.mean() == pytest.approx(0.0)


def test_features_do_not_use_future_prices() -> None:
    frame = make_frame(n_securities=3)
    cutoff = pd.Timestamp("2024-03-01")
    mutated = frame.copy()
    mutated.loc[mutated["date"] > cutoff, ["close", "adj_close", "volume"]] *= 1.5

    baseline = compute_features(prepare_panel(frame))
    changed = compute_features(prepare_panel(mutated))

    pd.testing.assert_frame_equal(
        baseline.loc[baseline["date"] <= cutoff].reset_index(drop=True),
        changed.loc[changed["date"] <= cutoff].reset_index(drop=True),
    )


def test_missing_bar_is_not_forward_filled() -> None:
    frame = make_frame()
    missing_day = pd.Timestamp("2024-02-15")
    frame = frame.drop(
        frame.loc[(frame["security_id"] == "SEC1") & (frame["date"] == missing_day)].index
    )

    features = compute_features(prepare_panel(frame))
    dates = pd.Series(sorted(frame["date"].unique()))
    position = int(dates.tolist().index(missing_day))

    assert pd.isna(row_at(features, "SEC1", dates.iloc[position])["momentum_5"])
    assert pd.isna(row_at(features, "SEC1", dates.iloc[position + 5])["momentum_5"])
    assert pd.notna(row_at(features, "SEC1", dates.iloc[position + 6])["momentum_5"])
    assert pd.isna(row_at(features, "SEC1", dates.iloc[position])["volatility_21"])
