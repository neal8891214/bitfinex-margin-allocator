"""Poll Scheduler 測試"""

import asyncio
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

from src.scheduler.poll_scheduler import PollScheduler
from src.storage.models import (
    Position,
    PositionSide,
    TriggerType,
)
from src.core.margin_allocator import RebalanceResult
from src.core.position_liquidator import LiquidationResult


@pytest.fixture
def mock_config():
    """建立 mock 配置"""
    config = MagicMock()
    config.monitor.poll_interval_sec = 1
    return config


@pytest.fixture
def mock_client():
    """建立 mock API client"""
    client = AsyncMock()
    client.get_positions = AsyncMock(return_value=[
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("500"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1.0"),
        ),
    ])
    client.get_derivatives_balance = AsyncMock(return_value=Decimal("1000"))
    return client


@pytest.fixture
def mock_risk_calculator():
    """建立 mock 風險計算器"""
    calc = AsyncMock()
    calc.calculate_target_margins = AsyncMock(
        return_value={"BTC": Decimal("500")}
    )
    return calc


@pytest.fixture
def mock_allocator():
    """建立 mock 保證金分配器"""
    allocator = AsyncMock()
    allocator.execute_rebalance = AsyncMock(
        return_value=RebalanceResult(
            success_count=0,
            fail_count=0,
            total_adjusted=Decimal("0"),
            adjustments=[],
        )
    )
    return allocator


@pytest.fixture
def mock_liquidator():
    """建立 mock 減倉執行器"""
    liquidator = AsyncMock()
    liquidator.execute_if_needed = AsyncMock(
        return_value=LiquidationResult(
            executed=False,
            reason="No margin gap",
            plans=[],
        )
    )
    return liquidator


@pytest.fixture
def mock_notifier():
    """建立 mock 通知器"""
    notifier = AsyncMock()
    notifier.send_adjustment_report = AsyncMock(return_value=True)
    notifier.send_liquidation_alert = AsyncMock(return_value=True)
    return notifier


@pytest.fixture
def mock_db():
    """建立 mock 資料庫"""
    db = AsyncMock()
    db.save_account_snapshot = AsyncMock(return_value=1)
    return db


@pytest.fixture
def scheduler(
    mock_config,
    mock_client,
    mock_risk_calculator,
    mock_allocator,
    mock_liquidator,
    mock_notifier,
    mock_db,
):
    """建立 PollScheduler"""
    return PollScheduler(
        config=mock_config,
        client=mock_client,
        risk_calculator=mock_risk_calculator,
        allocator=mock_allocator,
        liquidator=mock_liquidator,
        notifier=mock_notifier,
        db=mock_db,
    )


@pytest.mark.asyncio
async def test_run_once_basic(scheduler, mock_client, mock_allocator, mock_db):
    """測試單次執行基本流程"""
    await scheduler.run_once()

    # 確認取得倉位
    mock_client.get_positions.assert_called_once()

    # 確認取得餘額
    mock_client.get_derivatives_balance.assert_called()

    # 確認執行重平衡
    mock_allocator.execute_rebalance.assert_called_once()

    # 確認記錄快照
    mock_db.save_account_snapshot.assert_called_once()


@pytest.mark.asyncio
async def test_run_once_no_positions(mock_config, mock_client, mock_allocator, mock_notifier, mock_db):
    """測試沒有倉位時跳過重平衡"""
    mock_client.get_positions = AsyncMock(return_value=[])  # 沒有倉位

    scheduler = PollScheduler(
        config=mock_config,
        client=mock_client,
        risk_calculator=AsyncMock(),
        allocator=mock_allocator,
        liquidator=AsyncMock(),
        notifier=mock_notifier,
        db=mock_db,
    )

    await scheduler.run_once()

    # 確認取得倉位
    mock_client.get_positions.assert_called_once()

    # 不應該執行重平衡
    mock_allocator.execute_rebalance.assert_not_called()

    # 不應該記錄快照
    mock_db.save_account_snapshot.assert_not_called()


@pytest.mark.asyncio
async def test_run_once_with_adjustments(scheduler, mock_allocator, mock_notifier):
    """測試有調整時發送通知"""
    # 設定有成功的調整
    mock_allocator.execute_rebalance = AsyncMock(
        return_value=RebalanceResult(
            success_count=1,
            fail_count=0,
            total_adjusted=Decimal("100"),
            adjustments=[],
        )
    )

    await scheduler.run_once()

    # 確認發送調整報告
    mock_notifier.send_adjustment_report.assert_called_once()


@pytest.mark.asyncio
async def test_run_once_with_liquidation(scheduler, mock_liquidator, mock_notifier):
    """測試有減倉時發送通知"""
    # 設定有減倉計畫
    mock_liquidator.execute_if_needed = AsyncMock(
        return_value=LiquidationResult(
            executed=True,
            reason="Margin gap",
            plans=[MagicMock()],
            success_count=1,
            fail_count=0,
            total_released=Decimal("100"),
        )
    )

    await scheduler.run_once()

    # 確認發送減倉警報
    mock_notifier.send_liquidation_alert.assert_called_once()


@pytest.mark.asyncio
async def test_run_once_no_notification_when_no_changes(scheduler, mock_notifier):
    """測試沒有變化時不發送通知"""
    await scheduler.run_once()

    # 沒有調整時不應該發送報告
    mock_notifier.send_adjustment_report.assert_not_called()


@pytest.mark.asyncio
async def test_start_and_stop(scheduler):
    """測試啟動和停止"""
    # 啟動
    await scheduler.start()
    assert scheduler._running is True
    assert scheduler._task is not None

    # 等待一小段時間
    await asyncio.sleep(0.1)

    # 停止
    await scheduler.stop()
    assert scheduler._running is False
    assert scheduler._task is None


@pytest.mark.asyncio
async def test_start_already_running(scheduler):
    """測試重複啟動"""
    await scheduler.start()

    # 記住原始 task
    original_task = scheduler._task

    # 再次啟動應該不會建立新的 task
    await scheduler.start()
    assert scheduler._task is original_task

    await scheduler.stop()


@pytest.mark.asyncio
async def test_run_once_saves_account_snapshot(scheduler, mock_db, mock_client):
    """測試記錄帳戶快照"""
    await scheduler.run_once()

    # 確認儲存快照被呼叫
    mock_db.save_account_snapshot.assert_called_once()

    # 檢查快照內容
    call_args = mock_db.save_account_snapshot.call_args
    snapshot = call_args[0][0]

    assert snapshot.total_equity == Decimal("1500")  # 1000 + 500
    assert snapshot.total_margin == Decimal("500")
    assert snapshot.available_balance == Decimal("1000")
    assert len(snapshot.positions_json) == 1


@pytest.mark.asyncio
async def test_run_once_error_handling(scheduler, mock_client):
    """測試執行錯誤處理"""
    # 設定 API 錯誤
    mock_client.get_positions = AsyncMock(side_effect=Exception("API Error"))

    # 應該拋出異常
    with pytest.raises(Exception, match="API Error"):
        await scheduler.run_once()


@pytest.mark.asyncio
async def test_poll_loop_continues_on_error(mock_config, mock_client, mock_db):
    """測試輪詢迴圈在錯誤後繼續"""
    mock_config.monitor.poll_interval_sec = 0.1

    call_count = 0

    async def failing_then_success():
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise Exception("First call fails")
        return []

    mock_client.get_positions = AsyncMock(side_effect=failing_then_success)

    scheduler = PollScheduler(
        config=mock_config,
        client=mock_client,
        risk_calculator=AsyncMock(),
        allocator=AsyncMock(),
        liquidator=AsyncMock(),
        notifier=AsyncMock(),
        db=mock_db,
    )

    await scheduler.start()
    await asyncio.sleep(0.3)  # 等待幾次輪詢
    await scheduler.stop()

    # 應該多次呼叫（即使第一次失敗）
    assert call_count >= 2


@pytest.mark.asyncio
async def test_run_once_calculates_total_margin_correctly(scheduler, mock_client, mock_allocator):
    """測試正確計算總保證金"""
    # 設定多個倉位
    mock_client.get_positions = AsyncMock(return_value=[
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("500"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1.0"),
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
            margin_rate=Decimal("1.0"),
        ),
    ])
    mock_client.get_derivatives_balance = AsyncMock(return_value=Decimal("200"))

    await scheduler.run_once()

    # 確認 execute_rebalance 被呼叫時的 total_available
    # 應該是 200 (available) + 500 (BTC margin) + 300 (ETH margin) = 1000
    call_args = mock_allocator.execute_rebalance.call_args
    total_available = call_args[0][1]
    assert total_available == Decimal("1000")


@pytest.mark.asyncio
async def test_run_once_uses_scheduled_trigger_type(scheduler, mock_allocator):
    """測試使用 SCHEDULED 觸發類型"""
    await scheduler.run_once()

    # 確認使用 SCHEDULED 觸發類型
    call_args = mock_allocator.execute_rebalance.call_args
    trigger_type = call_args[0][2]
    assert trigger_type == TriggerType.SCHEDULED


@pytest.mark.asyncio
async def test_run_once_gets_updated_balance_for_liquidation(
    scheduler, mock_client, mock_liquidator
):
    """測試減倉檢查前重新取得餘額"""
    # 設定第一次和第二次呼叫回傳不同值
    mock_client.get_derivatives_balance = AsyncMock(
        side_effect=[Decimal("1000"), Decimal("1200")]
    )

    await scheduler.run_once()

    # 確認取得餘額被呼叫兩次
    assert mock_client.get_derivatives_balance.call_count == 2

    # 確認 liquidator 使用的是更新後的餘額
    call_args = mock_liquidator.execute_if_needed.call_args
    available_balance = call_args[0][1]
    assert available_balance == Decimal("1200")


@pytest.mark.asyncio
async def test_snapshot_positions_json_format(scheduler, mock_db, mock_client):
    """測試快照中的倉位 JSON 格式"""
    mock_client.get_positions = AsyncMock(return_value=[
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("1.5"),
            entry_price=Decimal("50000"),
            current_price=Decimal("51000"),
            margin=Decimal("500"),
            leverage=10,
            unrealized_pnl=Decimal("1500"),
            margin_rate=Decimal("0.65"),
        ),
    ])

    await scheduler.run_once()

    # 檢查 positions_json 格式
    call_args = mock_db.save_account_snapshot.call_args
    snapshot = call_args[0][0]
    pos_data = snapshot.positions_json[0]

    assert pos_data["symbol"] == "BTC"
    assert pos_data["side"] == "long"
    assert pos_data["quantity"] == "1.5"
    assert pos_data["current_price"] == "51000"
    assert pos_data["margin"] == "500"
    assert pos_data["margin_rate"] == "0.65"
