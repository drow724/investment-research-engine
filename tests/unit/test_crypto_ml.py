from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import numpy as np
import pytest

from investment.crypto.application.ml_service import (
    ActivateModelCommand,
    CryptoMLService,
    ModelActivationPolicy,
    PredictReturnsCommand,
    TrainModelCommand,
)
from investment.crypto.infrastructure.market_data import InMemoryCryptoMarketDataProvider
from investment.crypto.ml.dataset import CrossSectionalDatasetBuilder, UniverseMode
from investment.crypto.ml.model import ModelKind
from investment.crypto.ml.registry import CryptoModelRegistry
from investment.crypto.ml.split import PurgedWalkForwardConfig, PurgedWalkForwardSplitter
from tests.crypto_fixtures import crypto_bundle


def test_current_features_do_not_use_candle_unavailable_at_as_of() -> None:
    bundle = crypto_bundle(260)
    as_of = datetime(2023, 8, 20, tzinfo=UTC)
    builder = CrossSectionalDatasetBuilder(UniverseMode.STATIC_EXPLICIT)
    baseline = builder.build_current_features(bundle, as_of)
    candles = dict(bundle.candles)
    btc = list(candles["BTCKRW"])
    index = next(i for i, candle in enumerate(btc) if candle.open_time == as_of)
    btc[index] = replace(
        btc[index], close=btc[index].close * Decimal("10"), high=btc[index].high * Decimal("10")
    )
    candles["BTCKRW"] = tuple(btc)

    assert baseline.equals(builder.build_current_features(replace(bundle, candles=candles), as_of))


def test_purged_walk_forward_keeps_calendar_gaps() -> None:
    dataset = CrossSectionalDatasetBuilder(UniverseMode.STATIC_EXPLICIT).build(
        crypto_bundle(620),
        datetime(2023, 8, 1, tzinfo=UTC),
        datetime(2024, 7, 1, tzinfo=UTC),
        label_horizon_days=7,
    )
    splits = PurgedWalkForwardSplitter(PurgedWalkForwardConfig(120, 30, 30, 30, 7)).split(
        dataset.frame
    )
    assert splits
    for split in splits:
        train_last = split.train.get_column("as_of").max()
        validation_first = split.validation.get_column("as_of").min()
        validation_last = split.validation.get_column("as_of").max()
        test_first = split.test.get_column("as_of").min()
        assert validation_first - train_last >= timedelta(days=7)
        assert test_first - validation_last >= timedelta(days=7)


def test_train_register_and_predict_is_reproducible(tmp_path) -> None:
    bundle = crypto_bundle(1000)
    service = CryptoMLService(
        InMemoryCryptoMarketDataProvider(bundle), CryptoModelRegistry(tmp_path / "models")
    )
    command = TrainModelCommand(
        ("BTC/KRW", "ETH/KRW", "SOL/KRW"),
        datetime(2024, 2, 5, tzinfo=UTC),
        datetime(2025, 6, 1, tzinfo=UTC),
        UniverseMode.STATIC_EXPLICIT,
        7,
        (ModelKind.RIDGE, ModelKind.HIST_GRADIENT_BOOSTING),
        PurgedWalkForwardConfig(180, 45, 45, 45, 7),
        2,
    )
    trained = service.train(command)
    with pytest.raises(ValueError, match="activation policy rejected"):
        service.activate(
            ActivateModelCommand(trained.metadata.model_id, "researcher@example.com"),
            ModelActivationPolicy(minimum_validation_ic=2.0, minimum_test_ic=2.0),
        )
    activation = service.activate(
        ActivateModelCommand(trained.metadata.model_id, "researcher@example.com")
    )
    prediction_command = PredictReturnsCommand(
        command.pair_symbols, datetime(2025, 6, 15, tzinfo=UTC)
    )
    first = service.predict(prediction_command)
    second = service.predict(replace(prediction_command, model_id=trained.metadata.model_id))

    assert trained.rows > 0
    assert activation.activation.approved_by == "researcher@example.com"
    assert len(tuple((tmp_path / "models" / "activations").glob("*.json"))) == 1
    assert trained.metadata.limitations == ("STATIC_UNIVERSE_SURVIVORSHIP_RISK",)
    assert first == second
    assert len(first.predictions) == 3
    assert all(np.isfinite(item.expected_return) for item in first.predictions)
