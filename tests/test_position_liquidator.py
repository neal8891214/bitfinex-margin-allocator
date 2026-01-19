"""Position Liquidator 模組測試"""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from src.core.position_liquidator import PositionLiquidator, LiquidationPlan
from src.storage.models import Position, PositionSide


@pytest.fixture
def mock_config():
    """建立 mock 配置"""
    config = MagicMock()
    config.liquidation.enabled = True
    config.liquidation.dry_run = False
    config.liquidation.max_single_close_pct = 25
    config.liquidation.cooldown_seconds = 30
    config.liquidation.safety_margin_multiplier = 3.0
    config.position_priority = {"BTC": 100, "ETH": 90, "default": 50}
    config.get_position_priority = lambda s: config.position_priority.get(
        s, config.position_priority["default"]
    )
    return config


@pytest.fixture
def mock_client():
    """建立 mock Bitfinex client"""
    client = AsyncMock()
    client.close_position = AsyncMock(return_value=True)
    client.get_full_symbol = lambda s: f"t{s}F0:USTF0"
    return client


@pytest.fixture
def mock_db():
    """建立 mock database"""
    return AsyncMock()


@pytest.fixture
def liquidator(mock_config, mock_client, mock_db):
    """建立 PositionLiquidator"""
    return PositionLiquidator(mock_config, mock_client, mock_db)


def test_calculate_margin_gap():
    """測試計算保證金缺口"""
    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("500"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1"),
        ),
    ]

    config = MagicMock()
    config.liquidation.safety_margin_multiplier = 3.0

    liq = PositionLiquidator(config, AsyncMock(), AsyncMock())

    # 最低安全保證金 = 名義價值 * 0.5% * 3 = 50000 * 0.005 * 3 = 750
    # 當前保證金 = 500
    # 可用餘額 = 100
    # 缺口 = 750 - 500 - 100 = 150
    gap = liq._calculate_margin_gap(positions, Decimal("100"))
    assert gap == Decimal("150")


def test_calculate_margin_gap_no_gap():
    """測試無缺口情況"""
    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("1000"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("2"),
        ),
    ]

    config = MagicMock()
    config.liquidation.safety_margin_multiplier = 3.0

    liq = PositionLiquidator(config, AsyncMock(), AsyncMock())

    # 最低安全保證金 = 50000 * 0.005 * 3 = 750
    # 當前保證金 = 1000，可用餘額 = 100
    # 缺口 = 750 - 1000 - 100 = -350，應回傳 0
    gap = liq._calculate_margin_gap(positions, Decimal("100"))
    assert gap == Decimal("0")


def test_sort_by_priority():
    """測試按優先級排序"""
    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("500"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1"),
        ),
        Position(
            symbol="ETH",
            side=PositionSide.LONG,
            quantity=Decimal("10"),
            entry_price=Decimal("3000"),
            current_price=Decimal("3000"),
            margin=Decimal("300"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1"),
        ),
        Position(
            symbol="DOGE",
            side=PositionSide.LONG,
            quantity=Decimal("10000"),
            entry_price=Decimal("0.1"),
            current_price=Decimal("0.1"),
            margin=Decimal("100"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("10"),
        ),
    ]

    config = MagicMock()
    config.position_priority = {"BTC": 100, "ETH": 90, "default": 50}
    config.get_position_priority = lambda s: config.position_priority.get(
        s, config.position_priority["default"]
    )

    liq = PositionLiquidator(config, AsyncMock(), AsyncMock())
    sorted_positions = liq._sort_by_priority(positions)

    # DOGE (50) < ETH (90) < BTC (100)
    assert sorted_positions[0].symbol == "DOGE"
    assert sorted_positions[1].symbol == "ETH"
    assert sorted_positions[2].symbol == "BTC"


def test_create_liquidation_plan():
    """測試建立減倉計畫"""
    position = Position(
        symbol="DOGE",
        side=PositionSide.LONG,
        quantity=Decimal("10000"),
        entry_price=Decimal("0.1"),
        current_price=Decimal("0.1"),
        margin=Decimal("100"),
        leverage=10,
        unrealized_pnl=Decimal("0"),
        margin_rate=Decimal("10"),
    )

    config = MagicMock()
    config.liquidation.max_single_close_pct = 25

    liq = PositionLiquidator(config, AsyncMock(), AsyncMock())
    plan = liq._create_liquidation_plan(position, Decimal("50"))

    # 最多平倉 25% = 2500
    # 但只需要釋放 50 USDT 的保證金
    assert plan.symbol == "DOGE"
    assert plan.close_quantity == Decimal("2500")  # 25% of 10000


def test_create_liquidation_plan_limited_by_needed():
    """測試減倉計畫受需求限制"""
    position = Position(
        symbol="BTC",
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("50000"),
        current_price=Decimal("50000"),
        margin=Decimal("5000"),
        leverage=10,
        unrealized_pnl=Decimal("0"),
        margin_rate=Decimal("10"),
    )

    config = MagicMock()
    config.liquidation.max_single_close_pct = 25

    liq = PositionLiquidator(config, AsyncMock(), AsyncMock())
    # 需要釋放 500 USDT
    # margin_per_unit = 5000/1 = 5000
    # qty_for_release = 500/5000 = 0.1
    # max_close_qty = 1 * 0.25 = 0.25
    # close_qty = min(0.25, 0.1) = 0.1
    plan = liq._create_liquidation_plan(position, Decimal("500"))

    assert plan.close_quantity == Decimal("0.1")
    assert plan.estimated_release == Decimal("500")


@pytest.mark.asyncio
async def test_execute_liquidation_disabled(mock_config, mock_client, mock_db):
    """測試減倉功能停用"""
    mock_config.liquidation.enabled = False

    liq = PositionLiquidator(mock_config, mock_client, mock_db)

    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("100"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("0.2"),
        ),
    ]

    result = await liq.execute_if_needed(positions, Decimal("0"))

    assert result.executed is False
    assert result.reason == "Liquidation disabled"
    assert len(result.plans) == 0


@pytest.mark.asyncio
async def test_execute_liquidation_dry_run(mock_config, mock_client, mock_db):
    """測試 dry run 模式"""
    mock_config.liquidation.dry_run = True

    liq = PositionLiquidator(mock_config, mock_client, mock_db)

    positions = [
        Position(
            symbol="DOGE",
            side=PositionSide.LONG,
            quantity=Decimal("10000"),
            entry_price=Decimal("0.1"),
            current_price=Decimal("0.1"),
            margin=Decimal("50"),  # 很低的保證金
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("5"),
        ),
    ]

    # 名義價值 = 10000 * 0.1 = 1000
    # 最低安全保證金 = 1000 * 0.005 * 3 = 15
    # 當前保證金 = 50，可用餘額 = 0
    # 缺口 = 15 - 50 - 0 = -35，無缺口
    # 改用更低的保證金測試
    positions[0] = Position(
        symbol="DOGE",
        side=PositionSide.LONG,
        quantity=Decimal("100000"),
        entry_price=Decimal("0.1"),
        current_price=Decimal("0.1"),
        margin=Decimal("50"),
        leverage=10,
        unrealized_pnl=Decimal("0"),
        margin_rate=Decimal("0.5"),
    )

    # 名義價值 = 100000 * 0.1 = 10000
    # 最低安全保證金 = 10000 * 0.005 * 3 = 150
    # 缺口 = 150 - 50 - 0 = 100

    result = await liq.execute_if_needed(positions, Decimal("0"))

    assert result.executed is False
    assert result.reason == "Dry run mode"
    assert len(result.plans) > 0
    mock_client.close_position.assert_not_called()


@pytest.mark.asyncio
async def test_execute_liquidation_success(mock_config, mock_client, mock_db):
    """測試成功執行減倉"""
    liq = PositionLiquidator(mock_config, mock_client, mock_db)

    positions = [
        Position(
            symbol="DOGE",
            side=PositionSide.LONG,
            quantity=Decimal("100000"),
            entry_price=Decimal("0.1"),
            current_price=Decimal("0.1"),
            margin=Decimal("50"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("0.5"),
        ),
    ]

    result = await liq.execute_if_needed(positions, Decimal("0"))

    assert result.executed is True
    assert result.success_count == 1
    assert result.fail_count == 0
    mock_client.close_position.assert_called_once()
    mock_db.save_liquidation.assert_called_once()


@pytest.mark.asyncio
async def test_execute_liquidation_no_gap(mock_config, mock_client, mock_db):
    """測試無缺口時不執行減倉"""
    liq = PositionLiquidator(mock_config, mock_client, mock_db)

    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("5000"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("10"),
        ),
    ]

    result = await liq.execute_if_needed(positions, Decimal("1000"))

    assert result.executed is False
    assert result.reason == "No margin gap"
    mock_client.close_position.assert_not_called()


@pytest.mark.asyncio
async def test_cooldown_period(mock_config, mock_client, mock_db):
    """測試冷卻期機制"""
    import time

    liq = PositionLiquidator(mock_config, mock_client, mock_db)
    liq._last_liquidation_time = time.time()  # 設定剛執行過

    positions = [
        Position(
            symbol="DOGE",
            side=PositionSide.LONG,
            quantity=Decimal("100000"),
            entry_price=Decimal("0.1"),
            current_price=Decimal("0.1"),
            margin=Decimal("50"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("0.5"),
        ),
    ]

    result = await liq.execute_if_needed(positions, Decimal("0"))

    assert result.executed is False
    assert result.reason == "In cooldown period"


def test_check_cooldown_first_time(liquidator):
    """測試首次執行時無冷卻期"""
    assert liquidator._check_cooldown() is True


def test_check_cooldown_after_execution(liquidator):
    """測試執行後在冷卻期內"""
    import time

    liquidator._last_liquidation_time = time.time()
    assert liquidator._check_cooldown() is False


def test_check_cooldown_expired(mock_config, mock_client, mock_db):
    """測試冷卻期過後"""
    import time

    mock_config.liquidation.cooldown_seconds = 1
    liq = PositionLiquidator(mock_config, mock_client, mock_db)
    liq._last_liquidation_time = time.time() - 2  # 2 秒前

    assert liq._check_cooldown() is True
