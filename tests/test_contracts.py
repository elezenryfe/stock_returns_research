from datetime import UTC, date, datetime, timedelta

import pytest

from stock_returns.contracts import (
    ExperimentRecord,
    Feature,
    Label,
    Observation,
    Prediction,
    Security,
)
from stock_returns.errors import ContractError

UTC = UTC
T0 = datetime(2024, 1, 2, 21, 0, tzinfo=UTC)
LATER = T0 + timedelta(days=1)


def test_valid_security_and_listing_window() -> None:
    security = Security(
        security_id="SEC0001",
        ticker="AAA",
        name="Alpha Corp",
        exchange="XNYS",
        first_trade_date=date(2000, 1, 3),
        last_trade_date=date(2020, 6, 1),
    )

    assert security.is_listed_on(date(2010, 1, 4))
    assert not security.is_listed_on(date(2020, 6, 2))
    assert not security.is_listed_on(date(1999, 12, 31))


def test_security_rejects_reversed_listing_window() -> None:
    with pytest.raises(ContractError, match="last_trade_date"):
        Security(
            security_id="SEC0001",
            ticker="AAA",
            name="Alpha Corp",
            exchange="XNYS",
            first_trade_date=date(2020, 1, 2),
            last_trade_date=date(2019, 1, 2),
        )


def test_security_rejects_blank_identifier() -> None:
    with pytest.raises(ContractError, match="security_id"):
        Security(
            security_id=" ",
            ticker="AAA",
            name="Alpha Corp",
            exchange="XNYS",
            first_trade_date=date(2020, 1, 2),
        )


def test_valid_observation() -> None:
    observation = Observation(
        security_id="SEC0001",
        field="close",
        value=101.5,
        observed_at=T0,
        available_at=LATER,
        source="synthetic",
    )

    assert observation.value == 101.5


def test_observation_rejects_lookahead() -> None:
    with pytest.raises(ContractError, match="look-ahead"):
        Observation(
            security_id="SEC0001",
            field="close",
            value=101.5,
            observed_at=LATER,
            available_at=T0,
            source="synthetic",
        )


def test_observation_requires_timezone_aware_timestamps() -> None:
    with pytest.raises(ContractError, match="timezone-aware"):
        Observation(
            security_id="SEC0001",
            field="close",
            value=101.5,
            observed_at=datetime(2024, 1, 2, 21, 0),
            available_at=LATER,
            source="synthetic",
        )


def test_observation_rejects_non_finite_values() -> None:
    with pytest.raises(ContractError, match="finite"):
        Observation(
            security_id="SEC0001",
            field="close",
            value=float("nan"),
            observed_at=T0,
            available_at=LATER,
            source="synthetic",
        )


def test_feature_rejects_lookahead() -> None:
    with pytest.raises(ContractError, match="look-ahead"):
        Feature(
            security_id="SEC0001",
            name="momentum_21d",
            value=0.05,
            as_of=LATER,
            available_at=T0,
            source="computed",
        )


def test_valid_label_with_excess_return() -> None:
    label = Label(
        security_id="SEC0001",
        as_of=T0,
        horizon_days=21,
        forward_return=0.04,
        label_available_at=T0 + timedelta(days=30),
        benchmark_return=0.01,
        excess_return=0.03,
    )

    assert label.excess_return == pytest.approx(0.03)


@pytest.mark.parametrize("horizon", [0, -21, True])
def test_label_rejects_invalid_horizon(horizon: int) -> None:
    with pytest.raises(ContractError, match="horizon_days"):
        Label(
            security_id="SEC0001",
            as_of=T0,
            horizon_days=horizon,
            forward_return=0.04,
            label_available_at=LATER,
        )


def test_label_must_become_known_after_forecast() -> None:
    with pytest.raises(ContractError, match="after as_of"):
        Label(
            security_id="SEC0001",
            as_of=T0,
            horizon_days=21,
            forward_return=0.04,
            label_available_at=T0,
        )


def test_valid_prediction() -> None:
    prediction = Prediction(
        security_id="SEC0001",
        as_of=T0,
        horizon_days=21,
        expected_return=0.02,
        prob_positive=0.55,
        expected_volatility=0.08,
        model_id="baseline-v1",
    )

    assert prediction.prob_positive == pytest.approx(0.55)


@pytest.mark.parametrize("probability", [-0.1, 1.1, float("nan")])
def test_prediction_rejects_invalid_probability(probability: float) -> None:
    with pytest.raises(ContractError, match="prob_positive"):
        Prediction(
            security_id="SEC0001",
            as_of=T0,
            horizon_days=21,
            expected_return=0.02,
            prob_positive=probability,
            expected_volatility=0.08,
            model_id="baseline-v1",
        )


def test_prediction_rejects_negative_volatility() -> None:
    with pytest.raises(ContractError, match="expected_volatility"):
        Prediction(
            security_id="SEC0001",
            as_of=T0,
            horizon_days=21,
            expected_return=0.02,
            prob_positive=0.55,
            expected_volatility=-0.01,
            model_id="baseline-v1",
        )


def test_valid_experiment_record() -> None:
    record = ExperimentRecord(
        experiment_id="exp-001",
        created_at=T0,
        config_hash="abc123",
        dataset_id="synthetic-2024-01-02",
        model_name="ridge",
        notes="smoke test",
    )

    assert record.notes == "smoke test"


def test_experiment_requires_model_name() -> None:
    with pytest.raises(ContractError, match="model_name"):
        ExperimentRecord(
            experiment_id="exp-001",
            created_at=T0,
            config_hash="abc123",
            dataset_id="synthetic-2024-01-02",
            model_name="",
        )
