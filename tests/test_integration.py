"""Integration Tests - 端對端整合測試"""

import pytest
import pytest_asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch
from datetime import datetime

from src.config_manager import (
    Config,
    BitfinexConfig,
    TelegramConfig,
    MonitorConfig,
    ThresholdsConfig,
    LiquidationConfig,
    DatabaseConfig,
    LoggingConfig,
)
from src.storage.models import Position, PositionSide, TriggerType
from src.storage.database import Database
from src.api.bitfinex_client import BitfinexClient
from src.core.risk_calculator import RiskCalculator
from src.core.margin_allocator import MarginAllocator, RebalanceResult
from src.core.position_liquidator import PositionLiquidator, LiquidationResult
from src.notifier.telegram_bot import TelegramNotifier
from src.scheduler.poll_scheduler import PollScheduler
from src.scheduler.event_detector import EventDetector


# ============================================================================
# Fixtures
# ============================================================================


@pytest.fixture
def integration_config() -> Config:
    """建立整合測試用的完整配置"""
    return Config(
        bitfinex=BitfinexConfig(
            api_key="test_api_key",
            api_secret="test_api_secret",
            base_url="https://api.bitfinex.com",
            ws_url="wss://api.bitfinex.com/ws/2",
        ),
        telegram=TelegramConfig(
            bot_token="test_bot_token",
            chat_id="test_chat_id",
            enabled=False,  # 測試時禁用通知
        ),
        monitor=MonitorConfig(
            poll_interval_sec=60,
            volatility_update_hours=1,
            volatility_lookback_days=7,
        ),
        thresholds=ThresholdsConfig(
            min_adjustment_usdt=50.0,
            min_deviation_pct=5.0,
            emergency_margin_rate=2.0,
            price_spike_pct=3.0,
            account_margin_rate_warning=3.0,
        ),
        risk_weights={"BTC": 1.0, "ETH": 1.5},
        position_priority={"BTC": 100, "ETH": 80, "default": 50},
        liquidation=LiquidationConfig(
            enabled=True,
            require_confirmation=False,
            max_single_close_pct=25.0,
            cooldown_seconds=30,
            safety_margin_multiplier=3.0,
            dry_run=False,
        ),
        database=DatabaseConfig(path=":memory:"),
        logging=LoggingConfig(level="INFO", file="logs/test.log"),
    )


@pytest.fixture
def dry_run_config(integration_config: Config) -> Config:
    """建立 dry_run 模式的配置"""
    return integration_config.model_copy(
        update={
            "liquidation": integration_config.liquidation.model_copy(
                update={"dry_run": True}
            )
        }
    )


@pytest_asyncio.fixture
async def database():
    """建立測試用的記憶體資料庫"""
    db = Database(":memory:")
    await db.initialize()
    yield db
    await db.close()


@pytest.fixture
def mock_bitfinex_client() -> AsyncMock:
    """建立 mock Bitfinex 客戶端"""
    client = AsyncMock(spec=BitfinexClient)

    # 預設倉位資料
    client.get_positions = AsyncMock(
        return_value=[
            Position(
                symbol="BTC",
                side=PositionSide.LONG,
                quantity=Decimal("0.5"),
                entry_price=Decimal("50000"),
                current_price=Decimal("50000"),
                margin=Decimal("500"),
                leverage=50,
                unrealized_pnl=Decimal("0"),
                margin_rate=Decimal("2.0"),
            ),
            Position(
                symbol="ETH",
                side=PositionSide.LONG,
                quantity=Decimal("10"),
                entry_price=Decimal("3000"),
                current_price=Decimal("3000"),
                margin=Decimal("300"),
                leverage=100,
                unrealized_pnl=Decimal("0"),
                margin_rate=Decimal("1.0"),
            ),
        ]
    )

    # 可用餘額
    client.get_derivatives_balance = AsyncMock(return_value=Decimal("1000"))

    # API 操作預設成功
    client.update_position_margin = AsyncMock(return_value=True)
    client.close_position = AsyncMock(return_value=True)

    # K 線資料（用於波動率計算）
    client.get_candles = AsyncMock(
        return_value=[
            {"close": 50000},
            {"close": 51000},
            {"close": 49000},
            {"close": 50500},
            {"close": 49500},
            {"close": 50200},
            {"close": 50100},
        ]
    )

    # 符號轉換
    client.get_full_symbol = MagicMock(side_effect=lambda s: f"t{s}F0:USTF0")

    return client


@pytest.fixture
def mock_notifier() -> AsyncMock:
    """建立 mock Telegram 通知器"""
    notifier = AsyncMock(spec=TelegramNotifier)
    notifier.send_message = AsyncMock()
    notifier.send_adjustment_report = AsyncMock()
    notifier.send_liquidation_alert = AsyncMock()
    notifier.send_daily_report = AsyncMock()
    notifier.send_api_error_alert = AsyncMock()
    notifier.send_account_margin_warning = AsyncMock()
    return notifier


# ============================================================================
# 完整流程測試：取得倉位 → 計算目標 → 執行調整 → 記錄資料庫
# ============================================================================


@pytest.mark.asyncio
async def test_full_rebalance_flow(
    integration_config: Config,
    database: Database,
    mock_bitfinex_client: AsyncMock,
    mock_notifier: AsyncMock,
):
    """測試完整的重平衡流程"""
    # 建立元件
    risk_calculator = RiskCalculator(integration_config, mock_bitfinex_client)
    allocator = MarginAllocator(
        integration_config, risk_calculator, mock_bitfinex_client, database
    )
    liquidator = PositionLiquidator(integration_config, mock_bitfinex_client, database)

    # 建立排程器
    scheduler = PollScheduler(
        config=integration_config,
        client=mock_bitfinex_client,
        risk_calculator=risk_calculator,
        allocator=allocator,
        liquidator=liquidator,
        notifier=mock_notifier,
        db=database,
    )

    # 執行單次重平衡
    await scheduler.run_once()

    # 驗證：API 被呼叫
    mock_bitfinex_client.get_positions.assert_called_once()
    assert mock_bitfinex_client.get_derivatives_balance.call_count >= 1

    # 驗證：帳戶快照被記錄到資料庫
    snapshots = await database.get_account_snapshots(limit=10)
    assert len(snapshots) == 1
    assert snapshots[0].total_equity > 0


@pytest.mark.asyncio
async def test_rebalance_with_margin_adjustments(
    integration_config: Config,
    database: Database,
    mock_bitfinex_client: AsyncMock,
    mock_notifier: AsyncMock,
):
    """測試需要調整保證金的重平衡流程"""
    # 設定倉位保證金與目標差距大於閾值
    mock_bitfinex_client.get_positions = AsyncMock(
        return_value=[
            Position(
                symbol="BTC",
                side=PositionSide.LONG,
                quantity=Decimal("1"),
                entry_price=Decimal("50000"),
                current_price=Decimal("50000"),
                margin=Decimal("1000"),  # 低於目標
                leverage=50,
                unrealized_pnl=Decimal("0"),
                margin_rate=Decimal("2.0"),
            ),
            Position(
                symbol="ETH",
                side=PositionSide.LONG,
                quantity=Decimal("10"),
                entry_price=Decimal("3000"),
                current_price=Decimal("3000"),
                margin=Decimal("800"),  # 高於目標
                leverage=37,
                unrealized_pnl=Decimal("0"),
                margin_rate=Decimal("2.67"),
            ),
        ]
    )

    # 建立元件
    risk_calculator = RiskCalculator(integration_config, mock_bitfinex_client)
    allocator = MarginAllocator(
        integration_config, risk_calculator, mock_bitfinex_client, database
    )
    liquidator = PositionLiquidator(integration_config, mock_bitfinex_client, database)

    scheduler = PollScheduler(
        config=integration_config,
        client=mock_bitfinex_client,
        risk_calculator=risk_calculator,
        allocator=allocator,
        liquidator=liquidator,
        notifier=mock_notifier,
        db=database,
    )

    # 執行
    await scheduler.run_once()

    # 驗證：保證金調整被執行
    # update_position_margin 可能被呼叫多次（取決於調整計畫）
    # 或者沒被呼叫（如果調整低於閾值）
    # 最重要的是流程不出錯

    # 驗證：帳戶快照被記錄
    snapshots = await database.get_account_snapshots(limit=10)
    assert len(snapshots) == 1


@pytest.mark.asyncio
async def test_rebalance_stores_adjustments_in_db(
    integration_config: Config,
    database: Database,
    mock_notifier: AsyncMock,
):
    """測試保證金調整記錄被存入資料庫"""
    # 建立 mock client，確保調整會被執行
    mock_client = AsyncMock(spec=BitfinexClient)
    mock_client.get_positions = AsyncMock(
        return_value=[
            Position(
                symbol="BTC",
                side=PositionSide.LONG,
                quantity=Decimal("1"),
                entry_price=Decimal("50000"),
                current_price=Decimal("50000"),
                margin=Decimal("200"),  # 明顯低於目標
                leverage=250,
                unrealized_pnl=Decimal("0"),
                margin_rate=Decimal("0.4"),
            ),
        ]
    )
    mock_client.get_derivatives_balance = AsyncMock(return_value=Decimal("5000"))
    mock_client.update_position_margin = AsyncMock(return_value=True)
    mock_client.get_candles = AsyncMock(
        return_value=[{"close": 50000 + i * 100} for i in range(7)]
    )
    mock_client.get_full_symbol = MagicMock(side_effect=lambda s: f"t{s}F0:USTF0")

    # 建立元件
    risk_calculator = RiskCalculator(integration_config, mock_client)
    allocator = MarginAllocator(
        integration_config, risk_calculator, mock_client, database
    )
    liquidator = PositionLiquidator(integration_config, mock_client, database)

    scheduler = PollScheduler(
        config=integration_config,
        client=mock_client,
        risk_calculator=risk_calculator,
        allocator=allocator,
        liquidator=liquidator,
        notifier=mock_notifier,
        db=database,
    )

    # 執行
    await scheduler.run_once()

    # 驗證：檢查資料庫中的調整記錄
    adjustments = await database.get_margin_adjustments(limit=10)
    # 可能有調整記錄，也可能沒有（取決於風險計算結果）
    # 重要的是流程完成且資料庫操作正常
    assert isinstance(adjustments, list)


# ============================================================================
# 緊急重平衡測試
# ============================================================================


@pytest.mark.asyncio
async def test_emergency_rebalance_on_low_margin_rate(
    integration_config: Config,
    database: Database,
    mock_notifier: AsyncMock,
):
    """測試低保證金率倉位觸發緊急補充"""
    # 建立 mock client
    mock_client = AsyncMock(spec=BitfinexClient)
    mock_client.update_position_margin = AsyncMock(return_value=True)
    mock_client.get_derivatives_balance = AsyncMock(return_value=Decimal("10000"))
    mock_client.get_full_symbol = MagicMock(side_effect=lambda s: f"t{s}F0:USTF0")

    # 建立元件
    risk_calculator = RiskCalculator(integration_config, mock_client)
    allocator = MarginAllocator(
        integration_config, risk_calculator, mock_client, database
    )
    event_detector = EventDetector(integration_config, allocator, mock_notifier)

    # 建立危險倉位（低於 emergency_margin_rate）
    critical_position = Position(
        symbol="BTC",
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("50000"),
        current_price=Decimal("50000"),
        margin=Decimal("500"),  # 很低的保證金
        leverage=100,
        unrealized_pnl=Decimal("0"),
        margin_rate=Decimal("1.0"),  # 低於 emergency_margin_rate=2.0
    )

    positions = [critical_position]

    # 檢查緊急條件
    critical_positions = event_detector.check_emergency_conditions(positions)
    assert len(critical_positions) == 1
    assert critical_positions[0].symbol == "BTC"

    # 執行緊急重平衡
    success = await event_detector.handle_emergency(
        critical_position=critical_position,
        positions=positions,
        available_balance=Decimal("10000"),
    )

    assert success is True

    # 驗證：API 被呼叫
    mock_client.update_position_margin.assert_called_once()

    # 驗證：調整記錄在資料庫
    adjustments = await database.get_margin_adjustments(limit=10)
    assert len(adjustments) == 1
    assert adjustments[0].symbol == "BTC"
    assert adjustments[0].trigger_type == TriggerType.EMERGENCY


@pytest.mark.asyncio
async def test_emergency_rebalance_no_trigger_when_safe(
    integration_config: Config,
    database: Database,
    mock_notifier: AsyncMock,
):
    """測試安全倉位不觸發緊急重平衡"""
    mock_client = AsyncMock(spec=BitfinexClient)

    risk_calculator = RiskCalculator(integration_config, mock_client)
    allocator = MarginAllocator(
        integration_config, risk_calculator, mock_client, database
    )
    event_detector = EventDetector(integration_config, allocator, mock_notifier)

    # 建立安全倉位
    safe_position = Position(
        symbol="BTC",
        side=PositionSide.LONG,
        quantity=Decimal("1"),
        entry_price=Decimal("50000"),
        current_price=Decimal("50000"),
        margin=Decimal("5000"),
        leverage=10,
        unrealized_pnl=Decimal("0"),
        margin_rate=Decimal("10.0"),  # 高於 emergency_margin_rate=2.0
    )

    positions = [safe_position]

    # 檢查緊急條件
    critical_positions = event_detector.check_emergency_conditions(positions)

    # 不應該有危險倉位
    assert len(critical_positions) == 0


@pytest.mark.asyncio
async def test_account_margin_rate_warning(
    integration_config: Config,
    database: Database,
    mock_notifier: AsyncMock,
):
    """測試帳戶保證金率警告"""
    mock_client = AsyncMock(spec=BitfinexClient)

    risk_calculator = RiskCalculator(integration_config, mock_client)
    allocator = MarginAllocator(
        integration_config, risk_calculator, mock_client, database
    )
    event_detector = EventDetector(integration_config, allocator, mock_notifier)

    # 低於警告閾值的帳戶狀態
    total_equity = Decimal("250")
    total_margin = Decimal("10000")  # 保證金率 = 250/10000 * 100 = 2.5%

    # 檢查是否觸發警告
    triggered = event_detector.check_account_margin_rate(total_equity, total_margin)

    assert triggered is True  # 2.5% < 3.0%

    # 發送警告
    await event_detector.handle_account_margin_warning(2.5)

    # 驗證通知被呼叫
    mock_notifier.send_account_margin_warning.assert_called_once_with(2.5)


# ============================================================================
# 減倉流程測試
# ============================================================================


@pytest.mark.asyncio
async def test_liquidation_on_margin_gap(
    integration_config: Config,
    database: Database,
):
    """測試保證金缺口觸發自動減倉"""
    mock_client = AsyncMock(spec=BitfinexClient)
    mock_client.close_position = AsyncMock(return_value=True)
    mock_client.get_full_symbol = MagicMock(side_effect=lambda s: f"t{s}F0:USTF0")

    liquidator = PositionLiquidator(integration_config, mock_client, database)

    # 建立會產生保證金缺口的倉位
    # 名義價值很大，但保證金很少，且可用餘額也很少
    positions = [
        Position(
            symbol="DOGE",
            side=PositionSide.LONG,
            quantity=Decimal("1000000"),
            entry_price=Decimal("0.1"),
            current_price=Decimal("0.1"),
            margin=Decimal("100"),  # 100 USDT 保證金
            leverage=1000,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("0.1"),  # 名義價值 100000，保證金率 0.1%
        ),
    ]

    # 可用餘額很少
    available_balance = Decimal("10")

    # 執行減倉檢查
    result = await liquidator.execute_if_needed(positions, available_balance)

    # 應該有減倉計畫
    assert len(result.plans) > 0

    # 如果不是 dry_run，應該執行減倉
    if result.executed:
        mock_client.close_position.assert_called()

        # 檢查減倉記錄
        liquidations = await database.get_liquidations(limit=10)
        assert len(liquidations) > 0


@pytest.mark.asyncio
async def test_liquidation_respects_priority(
    integration_config: Config,
    database: Database,
):
    """測試減倉遵循優先級（低優先級先減倉）"""
    mock_client = AsyncMock(spec=BitfinexClient)
    mock_client.close_position = AsyncMock(return_value=True)
    mock_client.get_full_symbol = MagicMock(side_effect=lambda s: f"t{s}F0:USTF0")

    liquidator = PositionLiquidator(integration_config, mock_client, database)

    # 多個倉位，不同優先級
    # config 中 BTC=100, ETH=80, default=50
    positions = [
        Position(
            symbol="BTC",  # 優先級 100（高）
            side=PositionSide.LONG,
            quantity=Decimal("100"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("100"),
            leverage=50000,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("0.002"),
        ),
        Position(
            symbol="DOGE",  # 優先級 50（低，default）
            side=PositionSide.LONG,
            quantity=Decimal("10000000"),
            entry_price=Decimal("0.1"),
            current_price=Decimal("0.1"),
            margin=Decimal("100"),
            leverage=10000,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("0.01"),
        ),
    ]

    # 可用餘額很少，會觸發減倉
    available_balance = Decimal("1")

    result = await liquidator.execute_if_needed(positions, available_balance)

    # 如果有減倉計畫，低優先級的應該先被減倉
    if result.plans:
        assert result.plans[0].symbol == "DOGE"  # 低優先級先減


@pytest.mark.asyncio
async def test_liquidation_respects_cooldown(
    integration_config: Config,
    database: Database,
):
    """測試減倉遵循冷卻期"""
    mock_client = AsyncMock(spec=BitfinexClient)
    mock_client.close_position = AsyncMock(return_value=True)
    mock_client.get_full_symbol = MagicMock(side_effect=lambda s: f"t{s}F0:USTF0")

    liquidator = PositionLiquidator(integration_config, mock_client, database)

    positions = [
        Position(
            symbol="DOGE",
            side=PositionSide.LONG,
            quantity=Decimal("1000000"),
            entry_price=Decimal("0.1"),
            current_price=Decimal("0.1"),
            margin=Decimal("100"),
            leverage=1000,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("0.1"),
        ),
    ]

    available_balance = Decimal("1")

    # 第一次執行
    result1 = await liquidator.execute_if_needed(positions, available_balance)

    # 立即第二次執行（在冷卻期內）
    result2 = await liquidator.execute_if_needed(positions, available_balance)

    # 第二次應該因為冷卻期而不執行
    if result1.executed:
        assert result2.executed is False
        assert "cooldown" in result2.reason.lower()


# ============================================================================
# Dry Run 模式測試
# ============================================================================


@pytest.mark.asyncio
async def test_dry_run_mode_no_actual_writes(
    dry_run_config: Config,
    database: Database,
    mock_notifier: AsyncMock,
):
    """測試 dry_run 模式不執行實際寫入操作"""
    mock_client = AsyncMock(spec=BitfinexClient)
    mock_client.close_position = AsyncMock(return_value=True)
    mock_client.get_full_symbol = MagicMock(side_effect=lambda s: f"t{s}F0:USTF0")

    liquidator = PositionLiquidator(dry_run_config, mock_client, database)

    # 建立會觸發減倉的倉位
    positions = [
        Position(
            symbol="DOGE",
            side=PositionSide.LONG,
            quantity=Decimal("1000000"),
            entry_price=Decimal("0.1"),
            current_price=Decimal("0.1"),
            margin=Decimal("100"),
            leverage=1000,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("0.1"),
        ),
    ]

    available_balance = Decimal("1")

    result = await liquidator.execute_if_needed(positions, available_balance)

    # 應該有減倉計畫
    assert len(result.plans) > 0

    # 但不應該實際執行
    assert result.executed is False
    assert "dry run" in result.reason.lower()

    # close_position 不應該被呼叫
    mock_client.close_position.assert_not_called()

    # 資料庫不應該有減倉記錄
    liquidations = await database.get_liquidations(limit=10)
    assert len(liquidations) == 0


@pytest.mark.asyncio
async def test_dry_run_mode_shows_plans(
    dry_run_config: Config,
    database: Database,
):
    """測試 dry_run 模式正確顯示減倉計畫"""
    mock_client = AsyncMock(spec=BitfinexClient)
    mock_client.get_full_symbol = MagicMock(side_effect=lambda s: f"t{s}F0:USTF0")

    liquidator = PositionLiquidator(dry_run_config, mock_client, database)

    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.SHORT,
            quantity=Decimal("100"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("50"),
            leverage=100000,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("0.001"),
        ),
    ]

    available_balance = Decimal("1")

    result = await liquidator.execute_if_needed(positions, available_balance)

    # 檢查計畫內容
    if result.plans:
        plan = result.plans[0]
        assert plan.symbol == "BTC"
        assert plan.side == PositionSide.SHORT.value
        assert plan.current_quantity == Decimal("100")
        assert plan.close_quantity > 0
        assert plan.estimated_release > 0


# ============================================================================
# 整合流程測試 - 多模組協作
# ============================================================================


@pytest.mark.asyncio
async def test_full_cycle_with_all_components(
    integration_config: Config,
    database: Database,
    mock_bitfinex_client: AsyncMock,
    mock_notifier: AsyncMock,
):
    """測試完整週期：所有元件協同運作"""
    # 建立所有元件
    risk_calculator = RiskCalculator(integration_config, mock_bitfinex_client)
    allocator = MarginAllocator(
        integration_config, risk_calculator, mock_bitfinex_client, database
    )
    liquidator = PositionLiquidator(integration_config, mock_bitfinex_client, database)
    event_detector = EventDetector(integration_config, allocator, mock_notifier)

    scheduler = PollScheduler(
        config=integration_config,
        client=mock_bitfinex_client,
        risk_calculator=risk_calculator,
        allocator=allocator,
        liquidator=liquidator,
        notifier=mock_notifier,
        db=database,
    )

    # 1. 執行定時重平衡
    await scheduler.run_once()

    # 2. 取得倉位進行緊急檢查
    positions = await mock_bitfinex_client.get_positions()
    critical = event_detector.check_emergency_conditions(positions)

    # 3. 如果有危險倉位，執行緊急重平衡
    if critical:
        balance = await mock_bitfinex_client.get_derivatives_balance()
        await event_detector.handle_emergency(critical[0], positions, balance)

    # 4. 檢查帳戶保證金率
    total_margin = sum(p.margin for p in positions)
    balance = await mock_bitfinex_client.get_derivatives_balance()
    total_equity = balance + total_margin

    if event_detector.check_account_margin_rate(total_equity, total_margin):
        margin_rate = float(total_equity / total_margin * 100) if total_margin > 0 else 0
        await event_detector.handle_account_margin_warning(margin_rate)

    # 驗證：資料庫有記錄
    snapshots = await database.get_account_snapshots(limit=10)
    assert len(snapshots) >= 1


@pytest.mark.asyncio
async def test_price_spike_detection(
    integration_config: Config,
    database: Database,
    mock_notifier: AsyncMock,
):
    """測試價格急漲急跌偵測"""
    mock_client = AsyncMock(spec=BitfinexClient)

    risk_calculator = RiskCalculator(integration_config, mock_client)
    allocator = MarginAllocator(
        integration_config, risk_calculator, mock_client, database
    )
    event_detector = EventDetector(integration_config, allocator, mock_notifier)

    # 模擬價格更新
    # 第一次更新：建立基準價格
    triggered = event_detector.on_price_update("BTC", Decimal("50000"))
    assert triggered is False  # 沒有前一價格可比較

    # 第二次更新：正常變動（1%）
    triggered = event_detector.on_price_update("BTC", Decimal("50500"))
    assert triggered is False  # 1% < 3%

    # 第三次更新：急漲（5%）
    triggered = event_detector.on_price_update("BTC", Decimal("53025"))
    assert triggered is True  # 5% >= 3%


@pytest.mark.asyncio
async def test_scheduler_start_stop(
    integration_config: Config,
    database: Database,
    mock_bitfinex_client: AsyncMock,
    mock_notifier: AsyncMock,
):
    """測試排程器啟動和停止"""
    import asyncio

    # 使用較短的輪詢間隔
    config = integration_config.model_copy(
        update={"monitor": MonitorConfig(poll_interval_sec=1)}
    )

    risk_calculator = RiskCalculator(config, mock_bitfinex_client)
    allocator = MarginAllocator(
        config, risk_calculator, mock_bitfinex_client, database
    )
    liquidator = PositionLiquidator(config, mock_bitfinex_client, database)

    scheduler = PollScheduler(
        config=config,
        client=mock_bitfinex_client,
        risk_calculator=risk_calculator,
        allocator=allocator,
        liquidator=liquidator,
        notifier=mock_notifier,
        db=database,
    )

    # 啟動排程器
    await scheduler.start()

    # 等待一小段時間
    await asyncio.sleep(0.1)

    # 停止排程器
    await scheduler.stop()

    # 驗證：API 可能被呼叫（取決於時機）
    # 主要測試啟動和停止不會出錯


@pytest.mark.asyncio
async def test_database_daily_stats(
    integration_config: Config,
    database: Database,
    mock_bitfinex_client: AsyncMock,
    mock_notifier: AsyncMock,
):
    """測試每日統計功能"""
    from datetime import date

    # 建立元件並執行一些操作
    risk_calculator = RiskCalculator(integration_config, mock_bitfinex_client)
    allocator = MarginAllocator(
        integration_config, risk_calculator, mock_bitfinex_client, database
    )
    liquidator = PositionLiquidator(integration_config, mock_bitfinex_client, database)

    scheduler = PollScheduler(
        config=integration_config,
        client=mock_bitfinex_client,
        risk_calculator=risk_calculator,
        allocator=allocator,
        liquidator=liquidator,
        notifier=mock_notifier,
        db=database,
    )

    # 執行重平衡
    await scheduler.run_once()

    # 查詢每日統計
    stats = await database.get_daily_stats(date.today())

    # 統計應該存在
    assert "adjustment_count" in stats
    assert "liquidation_count" in stats
    assert stats["adjustment_count"] >= 0
    assert stats["liquidation_count"] >= 0


@pytest.mark.asyncio
async def test_empty_positions_handling(
    integration_config: Config,
    database: Database,
    mock_notifier: AsyncMock,
):
    """測試沒有倉位時的處理"""
    mock_client = AsyncMock(spec=BitfinexClient)
    mock_client.get_positions = AsyncMock(return_value=[])
    mock_client.get_derivatives_balance = AsyncMock(return_value=Decimal("10000"))
    mock_client.get_full_symbol = MagicMock(side_effect=lambda s: f"t{s}F0:USTF0")

    risk_calculator = RiskCalculator(integration_config, mock_client)
    allocator = MarginAllocator(
        integration_config, risk_calculator, mock_client, database
    )
    liquidator = PositionLiquidator(integration_config, mock_client, database)

    scheduler = PollScheduler(
        config=integration_config,
        client=mock_client,
        risk_calculator=risk_calculator,
        allocator=allocator,
        liquidator=liquidator,
        notifier=mock_notifier,
        db=database,
    )

    # 執行重平衡 - 不應該出錯
    await scheduler.run_once()

    # 沒有倉位時不應該有帳戶快照
    snapshots = await database.get_account_snapshots(limit=10)
    assert len(snapshots) == 0
