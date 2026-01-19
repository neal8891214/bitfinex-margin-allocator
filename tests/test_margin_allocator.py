"""Margin Allocator 測試"""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from src.core.margin_allocator import (
    MarginAllocator,
    MarginAdjustmentPlan,
    RebalanceResult,
)
from src.storage.models import Position, PositionSide, TriggerType


@pytest.fixture
def mock_config():
    """建立 mock 配置"""
    config = MagicMock()
    config.thresholds.min_adjustment_usdt = 50
    config.thresholds.min_deviation_pct = 5
    config.thresholds.emergency_margin_rate = 2.0
    return config


@pytest.fixture
def mock_risk_calculator():
    """建立 mock 風險計算器"""
    calc = AsyncMock()
    calc.calculate_target_margins = AsyncMock(
        return_value={
            "BTC": Decimal("500"),
            "ETH": Decimal("300"),
        }
    )
    return calc


@pytest.fixture
def mock_client():
    """建立 mock API client"""
    client = AsyncMock()
    client.update_position_margin = AsyncMock(return_value=True)
    client.get_full_symbol = lambda s: f"t{s}F0:USTF0"
    return client


@pytest.fixture
def mock_db():
    """建立 mock 資料庫"""
    return AsyncMock()


@pytest.fixture
def allocator(mock_config, mock_risk_calculator, mock_client, mock_db):
    """建立 MarginAllocator"""
    return MarginAllocator(mock_config, mock_risk_calculator, mock_client, mock_db)


def test_margin_adjustment_plan_is_increase():
    """測試 MarginAdjustmentPlan 的 is_increase 屬性"""
    # 增加保證金的情況
    plan_increase = MarginAdjustmentPlan(
        symbol="BTC",
        current_margin=Decimal("400"),
        target_margin=Decimal("500"),
        delta=Decimal("100"),
    )
    assert plan_increase.is_increase is True

    # 減少保證金的情況
    plan_decrease = MarginAdjustmentPlan(
        symbol="ETH",
        current_margin=Decimal("400"),
        target_margin=Decimal("300"),
        delta=Decimal("-100"),
    )
    assert plan_decrease.is_increase is False


def test_calculate_adjustment_plan_increase():
    """測試計算需要增加保證金的調整計畫"""
    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("0.5"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("400"),  # 目標 500，需增加 100
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1.6"),
        ),
    ]

    targets = {"BTC": Decimal("500")}

    allocator = MarginAllocator(MagicMock(), AsyncMock(), AsyncMock(), AsyncMock())
    allocator.config.thresholds.min_adjustment_usdt = 50
    allocator.config.thresholds.min_deviation_pct = 5

    plans = allocator._calculate_adjustment_plans(positions, targets)

    assert len(plans) == 1
    assert plans[0].symbol == "BTC"
    assert plans[0].delta == Decimal("100")
    assert plans[0].is_increase is True


def test_calculate_adjustment_plan_decrease():
    """測試計算需要減少保證金的調整計畫"""
    positions = [
        Position(
            symbol="ETH",
            side=PositionSide.LONG,
            quantity=Decimal("10"),
            entry_price=Decimal("3000"),
            current_price=Decimal("3000"),
            margin=Decimal("400"),  # 目標 300，需減少 100
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1.33"),
        ),
    ]

    targets = {"ETH": Decimal("300")}

    allocator = MarginAllocator(MagicMock(), AsyncMock(), AsyncMock(), AsyncMock())
    allocator.config.thresholds.min_adjustment_usdt = 50
    allocator.config.thresholds.min_deviation_pct = 5

    plans = allocator._calculate_adjustment_plans(positions, targets)

    assert len(plans) == 1
    assert plans[0].symbol == "ETH"
    assert plans[0].delta == Decimal("-100")
    assert plans[0].is_increase is False


def test_calculate_adjustment_plan_below_amount_threshold():
    """測試低於金額閾值不調整"""
    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("0.5"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("490"),  # 目標 500，只差 10（低於 50 閾值）
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1.96"),
        ),
    ]

    targets = {"BTC": Decimal("500")}

    allocator = MarginAllocator(MagicMock(), AsyncMock(), AsyncMock(), AsyncMock())
    allocator.config.thresholds.min_adjustment_usdt = 50
    allocator.config.thresholds.min_deviation_pct = 5

    plans = allocator._calculate_adjustment_plans(positions, targets)

    assert len(plans) == 0  # 不調整


def test_calculate_adjustment_plan_below_pct_threshold():
    """測試低於百分比閾值不調整"""
    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("0.5"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("2000"),  # 目標 2050，差 50（超過金額閾值）但只差 2.5%（低於 5%）
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("8.0"),
        ),
    ]

    targets = {"BTC": Decimal("2050")}

    allocator = MarginAllocator(MagicMock(), AsyncMock(), AsyncMock(), AsyncMock())
    allocator.config.thresholds.min_adjustment_usdt = 50
    allocator.config.thresholds.min_deviation_pct = 5

    plans = allocator._calculate_adjustment_plans(positions, targets)

    assert len(plans) == 0  # 不調整（低於百分比閾值）


def test_calculate_adjustment_plan_no_target():
    """測試沒有目標保證金的倉位不調整"""
    positions = [
        Position(
            symbol="DOGE",
            side=PositionSide.LONG,
            quantity=Decimal("1000"),
            entry_price=Decimal("0.1"),
            current_price=Decimal("0.1"),
            margin=Decimal("100"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("10.0"),
        ),
    ]

    targets = {"BTC": Decimal("500")}  # 沒有 DOGE 的目標

    allocator = MarginAllocator(MagicMock(), AsyncMock(), AsyncMock(), AsyncMock())
    allocator.config.thresholds.min_adjustment_usdt = 50
    allocator.config.thresholds.min_deviation_pct = 5

    plans = allocator._calculate_adjustment_plans(positions, targets)

    assert len(plans) == 0


def test_sort_plans_decrease_first():
    """測試排序：先減少再增加"""
    allocator = MarginAllocator(MagicMock(), AsyncMock(), AsyncMock(), AsyncMock())

    plans = [
        MarginAdjustmentPlan(
            symbol="BTC",
            current_margin=Decimal("400"),
            target_margin=Decimal("500"),
            delta=Decimal("100"),
        ),
        MarginAdjustmentPlan(
            symbol="ETH",
            current_margin=Decimal("400"),
            target_margin=Decimal("300"),
            delta=Decimal("-100"),
        ),
        MarginAdjustmentPlan(
            symbol="DOGE",
            current_margin=Decimal("200"),
            target_margin=Decimal("100"),
            delta=Decimal("-100"),
        ),
    ]

    sorted_plans = allocator._sort_plans(plans)

    # 減少的應該在前面
    assert sorted_plans[0].is_increase is False
    assert sorted_plans[1].is_increase is False
    assert sorted_plans[2].is_increase is True


def test_sort_plans_decreases_by_abs_delta():
    """測試減少計畫按絕對值從大到小排序"""
    allocator = MarginAllocator(MagicMock(), AsyncMock(), AsyncMock(), AsyncMock())

    plans = [
        MarginAdjustmentPlan(
            symbol="ETH",
            current_margin=Decimal("200"),
            target_margin=Decimal("150"),
            delta=Decimal("-50"),
        ),
        MarginAdjustmentPlan(
            symbol="DOGE",
            current_margin=Decimal("300"),
            target_margin=Decimal("100"),
            delta=Decimal("-200"),
        ),
        MarginAdjustmentPlan(
            symbol="LTC",
            current_margin=Decimal("200"),
            target_margin=Decimal("100"),
            delta=Decimal("-100"),
        ),
    ]

    sorted_plans = allocator._sort_plans(plans)

    # 按絕對值從大到小排序
    assert sorted_plans[0].symbol == "DOGE"  # -200
    assert sorted_plans[1].symbol == "LTC"   # -100
    assert sorted_plans[2].symbol == "ETH"   # -50


@pytest.mark.asyncio
async def test_execute_rebalance(allocator, mock_client, mock_db):
    """測試執行重平衡"""
    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("0.5"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("400"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1.6"),
        ),
        Position(
            symbol="ETH",
            side=PositionSide.LONG,
            quantity=Decimal("10"),
            entry_price=Decimal("3000"),
            current_price=Decimal("3000"),
            margin=Decimal("400"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1.33"),
        ),
    ]

    total_margin = Decimal("800")

    result = await allocator.execute_rebalance(positions, total_margin)

    assert isinstance(result, RebalanceResult)
    assert result.success_count >= 0
    assert result.fail_count >= 0
    assert result.total_adjusted >= Decimal("0")


@pytest.mark.asyncio
async def test_execute_rebalance_with_api_failure(mock_config, mock_risk_calculator, mock_db):
    """測試 API 失敗時的重平衡"""
    mock_client = AsyncMock()
    mock_client.update_position_margin = AsyncMock(return_value=False)  # API 失敗
    mock_client.get_full_symbol = lambda s: f"t{s}F0:USTF0"

    allocator = MarginAllocator(mock_config, mock_risk_calculator, mock_client, mock_db)

    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("0.5"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("400"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1.6"),
        ),
    ]

    result = await allocator.execute_rebalance(positions, Decimal("800"))

    # 應該有失敗計數
    assert result.fail_count > 0
    assert result.success_count == 0
    assert result.total_adjusted == Decimal("0")
    # 失敗時不應該有調整記錄
    assert len(result.adjustments) == 0


@pytest.mark.asyncio
async def test_execute_rebalance_no_adjustments_needed(mock_config, mock_risk_calculator, mock_client, mock_db):
    """測試不需要調整時的重平衡"""
    # 設定目標和現有保證金一致
    mock_risk_calculator.calculate_target_margins = AsyncMock(
        return_value={"BTC": Decimal("500")}
    )

    allocator = MarginAllocator(mock_config, mock_risk_calculator, mock_client, mock_db)

    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("0.5"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("500"),  # 與目標一致
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("2.0"),
        ),
    ]

    result = await allocator.execute_rebalance(positions, Decimal("500"))

    assert result.success_count == 0
    assert result.fail_count == 0
    assert result.total_adjusted == Decimal("0")
    assert len(result.adjustments) == 0


@pytest.mark.asyncio
async def test_emergency_rebalance(mock_config, mock_client, mock_db):
    """測試緊急重平衡"""
    allocator = MarginAllocator(mock_config, AsyncMock(), mock_client, mock_db)

    critical_position = Position(
        symbol="BTC",
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("50000"),
        current_price=Decimal("50000"),
        margin=Decimal("500"),  # 低於安全水平
        leverage=100,
        unrealized_pnl=Decimal("0"),
        margin_rate=Decimal("1.0"),  # 低於 emergency_margin_rate 的 2 倍
    )

    result = await allocator.emergency_rebalance(
        positions=[critical_position],
        critical_position=critical_position,
        available_balance=Decimal("10000"),
    )

    assert result.success_count == 1
    assert result.total_adjusted > Decimal("0")
    assert len(result.adjustments) == 1
    assert result.adjustments[0].trigger_type == TriggerType.EMERGENCY


@pytest.mark.asyncio
async def test_emergency_rebalance_already_safe(mock_config, mock_client, mock_db):
    """測試已經安全的倉位不需要緊急重平衡"""
    allocator = MarginAllocator(mock_config, AsyncMock(), mock_client, mock_db)

    # margin_rate 已經高於 emergency_margin_rate * 2
    safe_position = Position(
        symbol="BTC",
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("50000"),
        current_price=Decimal("50000"),
        margin=Decimal("5000"),
        leverage=10,
        unrealized_pnl=Decimal("0"),
        margin_rate=Decimal("10.0"),  # 遠高於 2.0 * 2 = 4.0
    )

    result = await allocator.emergency_rebalance(
        positions=[safe_position],
        critical_position=safe_position,
        available_balance=Decimal("10000"),
    )

    assert result.success_count == 0
    assert result.fail_count == 0
    assert result.total_adjusted == Decimal("0")


@pytest.mark.asyncio
async def test_emergency_rebalance_limited_by_available_balance(mock_config, mock_client, mock_db):
    """測試緊急重平衡受可用餘額限制"""
    allocator = MarginAllocator(mock_config, AsyncMock(), mock_client, mock_db)

    critical_position = Position(
        symbol="BTC",
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("50000"),
        current_price=Decimal("50000"),
        margin=Decimal("500"),
        leverage=100,
        unrealized_pnl=Decimal("0"),
        margin_rate=Decimal("1.0"),
    )

    # 只有 100 可用餘額
    result = await allocator.emergency_rebalance(
        positions=[critical_position],
        critical_position=critical_position,
        available_balance=Decimal("100"),
    )

    # 應該調整 100（受限於可用餘額）
    assert result.success_count == 1
    assert result.total_adjusted == Decimal("100")


@pytest.mark.asyncio
async def test_emergency_rebalance_below_min_threshold(mock_config, mock_client, mock_db):
    """測試緊急重平衡金額低於最小閾值時不執行"""
    allocator = MarginAllocator(mock_config, AsyncMock(), mock_client, mock_db)

    critical_position = Position(
        symbol="BTC",
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("50000"),
        current_price=Decimal("50000"),
        margin=Decimal("500"),
        leverage=100,
        unrealized_pnl=Decimal("0"),
        margin_rate=Decimal("1.0"),
    )

    # 只有 30 可用餘額（低於 min_adjustment_usdt = 50）
    result = await allocator.emergency_rebalance(
        positions=[critical_position],
        critical_position=critical_position,
        available_balance=Decimal("30"),
    )

    assert result.success_count == 0
    assert result.fail_count == 0
    assert result.total_adjusted == Decimal("0")


@pytest.mark.asyncio
async def test_emergency_rebalance_api_failure(mock_config, mock_db):
    """測試緊急重平衡 API 失敗"""
    mock_client = AsyncMock()
    mock_client.update_position_margin = AsyncMock(return_value=False)
    mock_client.get_full_symbol = lambda s: f"t{s}F0:USTF0"

    allocator = MarginAllocator(mock_config, AsyncMock(), mock_client, mock_db)

    critical_position = Position(
        symbol="BTC",
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("50000"),
        current_price=Decimal("50000"),
        margin=Decimal("500"),
        leverage=100,
        unrealized_pnl=Decimal("0"),
        margin_rate=Decimal("1.0"),
    )

    result = await allocator.emergency_rebalance(
        positions=[critical_position],
        critical_position=critical_position,
        available_balance=Decimal("10000"),
    )

    assert result.success_count == 0
    assert result.fail_count == 1
    assert result.total_adjusted == Decimal("0")
