"""測試資料模型"""

import pytest
from datetime import datetime
from decimal import Decimal
from src.storage.models import (
    Position,
    MarginAdjustment,
    Liquidation,
    AccountSnapshot,
    AdjustmentDirection,
    TriggerType,
    PositionSide,
)


def test_position_model():
    """測試 Position 資料模型"""
    pos = Position(
        symbol="BTC",
        side=PositionSide.LONG,
        quantity=Decimal("0.5"),
        entry_price=Decimal("50000"),
        current_price=Decimal("51000"),
        margin=Decimal("500"),
        leverage=10,
        unrealized_pnl=Decimal("500"),
        margin_rate=Decimal("5.0"),
    )

    assert pos.symbol == "BTC"
    assert pos.side == PositionSide.LONG
    assert pos.notional_value == Decimal("25500")  # 0.5 * 51000
    assert pos.is_profitable is True


def test_position_notional_value_short():
    """測試 Short 倉位的名義價值"""
    pos = Position(
        symbol="ETH",
        side=PositionSide.SHORT,
        quantity=Decimal("10"),
        entry_price=Decimal("3000"),
        current_price=Decimal("2900"),
        margin=Decimal("300"),
        leverage=10,
        unrealized_pnl=Decimal("1000"),
        margin_rate=Decimal("10.0"),
    )

    assert pos.notional_value == Decimal("29000")  # 10 * 2900
    assert pos.is_profitable is True


def test_position_not_profitable():
    """測試虧損倉位"""
    pos = Position(
        symbol="BTC",
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("50000"),
        current_price=Decimal("49000"),
        margin=Decimal("500"),
        leverage=10,
        unrealized_pnl=Decimal("-1000"),
        margin_rate=Decimal("5.0"),
    )

    assert pos.is_profitable is False


def test_margin_adjustment_model():
    """測試 MarginAdjustment 資料模型"""
    adj = MarginAdjustment(
        timestamp=datetime(2026, 1, 19, 12, 0, 0),
        symbol="BTC",
        direction=AdjustmentDirection.INCREASE,
        amount=Decimal("100"),
        before_margin=Decimal("400"),
        after_margin=Decimal("500"),
        trigger_type=TriggerType.SCHEDULED,
    )

    assert adj.symbol == "BTC"
    assert adj.direction == AdjustmentDirection.INCREASE
    assert adj.amount == Decimal("100")
    assert adj.id is None


def test_margin_adjustment_with_id():
    """測試帶 ID 的 MarginAdjustment"""
    adj = MarginAdjustment(
        id=1,
        timestamp=datetime(2026, 1, 19, 12, 0, 0),
        symbol="ETH",
        direction=AdjustmentDirection.DECREASE,
        amount=Decimal("50"),
        before_margin=Decimal("500"),
        after_margin=Decimal("450"),
        trigger_type=TriggerType.EMERGENCY,
    )

    assert adj.id == 1
    assert adj.direction == AdjustmentDirection.DECREASE
    assert adj.trigger_type == TriggerType.EMERGENCY


def test_liquidation_model():
    """測試 Liquidation 資料模型"""
    liq = Liquidation(
        timestamp=datetime(2026, 1, 19, 12, 0, 0),
        symbol="DOGE",
        side=PositionSide.LONG,
        quantity=Decimal("1000"),
        price=Decimal("0.1"),
        released_margin=Decimal("50"),
        reason="Insufficient margin",
    )

    assert liq.symbol == "DOGE"
    assert liq.released_margin == Decimal("50")
    assert liq.reason == "Insufficient margin"
    assert liq.id is None


def test_account_snapshot_model():
    """測試 AccountSnapshot 資料模型"""
    positions = [
        {"symbol": "BTC", "margin": 500},
        {"symbol": "ETH", "margin": 300},
    ]
    snap = AccountSnapshot(
        timestamp=datetime(2026, 1, 19, 12, 0, 0),
        total_equity=Decimal("10000"),
        total_margin=Decimal("800"),
        available_balance=Decimal("9200"),
        positions_json=positions,
    )

    assert snap.total_equity == Decimal("10000")
    assert len(snap.positions_json) == 2
    assert snap.id is None
    assert snap.positions_json[0]["symbol"] == "BTC"


def test_position_side_enum_values():
    """測試 PositionSide enum 值"""
    assert PositionSide.LONG.value == "long"
    assert PositionSide.SHORT.value == "short"


def test_adjustment_direction_enum_values():
    """測試 AdjustmentDirection enum 值"""
    assert AdjustmentDirection.INCREASE.value == "increase"
    assert AdjustmentDirection.DECREASE.value == "decrease"


def test_trigger_type_enum_values():
    """測試 TriggerType enum 值"""
    assert TriggerType.SCHEDULED.value == "scheduled"
    assert TriggerType.EMERGENCY.value == "emergency"
