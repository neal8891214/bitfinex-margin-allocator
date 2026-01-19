"""測試 Telegram 通知模組"""

from datetime import datetime
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.notifier.telegram_bot import TelegramNotifier
from src.core.margin_allocator import RebalanceResult, MarginAdjustmentPlan
from src.core.position_liquidator import LiquidationResult, LiquidationPlan
from src.storage.models import (
    MarginAdjustment,
    AdjustmentDirection,
    TriggerType,
)


@pytest_asyncio.fixture
async def mock_bot():
    """建立 mock Bot 物件"""
    with patch("src.notifier.telegram_bot.Bot") as MockBot:
        mock_instance = MagicMock()
        mock_instance.send_message = AsyncMock(return_value=MagicMock())
        MockBot.return_value = mock_instance
        yield mock_instance


@pytest_asyncio.fixture
async def notifier(mock_bot):
    """建立 TelegramNotifier 實例"""
    return TelegramNotifier(
        bot_token="test_token",
        chat_id="test_chat_id",
        enabled=True,
    )


@pytest_asyncio.fixture
async def disabled_notifier():
    """建立停用的 TelegramNotifier 實例"""
    with patch("src.notifier.telegram_bot.Bot"):
        return TelegramNotifier(
            bot_token="test_token",
            chat_id="test_chat_id",
            enabled=False,
        )


class TestSendMessage:
    """測試 send_message 方法"""

    @pytest.mark.asyncio
    async def test_send_message_success(self, notifier, mock_bot):
        """測試成功發送訊息"""
        result = await notifier.send_message("Test message")

        assert result is True
        mock_bot.send_message.assert_called_once_with(
            chat_id="test_chat_id",
            text="Test message",
            parse_mode="HTML",
        )

    @pytest.mark.asyncio
    async def test_send_message_disabled(self, disabled_notifier):
        """測試停用時不發送"""
        result = await disabled_notifier.send_message("Test message")

        assert result is True
        # _bot.send_message 不應被呼叫

    @pytest.mark.asyncio
    async def test_send_message_error(self, notifier, mock_bot):
        """測試發送錯誤時返回 False"""
        from telegram.error import TelegramError

        mock_bot.send_message.side_effect = TelegramError("Connection error")

        result = await notifier.send_message("Test message")

        assert result is False


class TestSendAdjustmentReport:
    """測試 send_adjustment_report 方法"""

    @pytest.mark.asyncio
    async def test_send_adjustment_report_success(self, notifier, mock_bot):
        """測試成功發送調整報告"""
        adjustments = [
            MarginAdjustment(
                timestamp=datetime.now(),
                symbol="BTC",
                direction=AdjustmentDirection.INCREASE,
                amount=Decimal("100.50"),
                before_margin=Decimal("500.00"),
                after_margin=Decimal("600.50"),
                trigger_type=TriggerType.SCHEDULED,
            ),
            MarginAdjustment(
                timestamp=datetime.now(),
                symbol="ETH",
                direction=AdjustmentDirection.DECREASE,
                amount=Decimal("50.25"),
                before_margin=Decimal("300.00"),
                after_margin=Decimal("249.75"),
                trigger_type=TriggerType.SCHEDULED,
            ),
        ]

        result = RebalanceResult(
            success_count=2,
            fail_count=0,
            total_adjusted=Decimal("150.75"),
            adjustments=adjustments,
        )

        success = await notifier.send_adjustment_report(result)

        assert success is True
        mock_bot.send_message.assert_called_once()
        call_args = mock_bot.send_message.call_args
        text = call_args[1]["text"]

        assert "保證金調整報告" in text
        assert "BTC" in text
        assert "ETH" in text
        assert "成功: 2" in text
        assert "150.75" in text

    @pytest.mark.asyncio
    async def test_send_adjustment_report_with_failures(self, notifier, mock_bot):
        """測試帶有失敗的調整報告"""
        result = RebalanceResult(
            success_count=1,
            fail_count=1,
            total_adjusted=Decimal("100.00"),
            adjustments=[
                MarginAdjustment(
                    timestamp=datetime.now(),
                    symbol="BTC",
                    direction=AdjustmentDirection.INCREASE,
                    amount=Decimal("100.00"),
                    before_margin=Decimal("500.00"),
                    after_margin=Decimal("600.00"),
                    trigger_type=TriggerType.EMERGENCY,
                ),
            ],
        )

        success = await notifier.send_adjustment_report(result)

        assert success is True
        text = mock_bot.send_message.call_args[1]["text"]
        assert "失敗: 1" in text

    @pytest.mark.asyncio
    async def test_send_adjustment_report_empty(self, notifier, mock_bot):
        """測試空調整報告不發送"""
        result = RebalanceResult(
            success_count=0,
            fail_count=0,
            total_adjusted=Decimal("0"),
            adjustments=[],
        )

        success = await notifier.send_adjustment_report(result)

        assert success is True
        mock_bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_send_adjustment_report_disabled(self, disabled_notifier):
        """測試停用時不發送"""
        result = RebalanceResult(
            success_count=1,
            fail_count=0,
            total_adjusted=Decimal("100.00"),
            adjustments=[],
        )

        success = await disabled_notifier.send_adjustment_report(result)
        assert success is True


class TestSendLiquidationAlert:
    """測試 send_liquidation_alert 方法"""

    @pytest.mark.asyncio
    async def test_send_liquidation_alert_executed(self, notifier, mock_bot):
        """測試已執行的減倉警報"""
        plans = [
            LiquidationPlan(
                symbol="BTC",
                side="LONG",
                current_quantity=Decimal("0.5"),
                close_quantity=Decimal("0.1"),
                current_price=Decimal("50000"),
                estimated_release=Decimal("1000"),
            ),
        ]

        result = LiquidationResult(
            executed=True,
            reason="Margin gap: 500",
            plans=plans,
            success_count=1,
            fail_count=0,
            total_released=Decimal("1000"),
        )

        success = await notifier.send_liquidation_alert(result)

        assert success is True
        text = mock_bot.send_message.call_args[1]["text"]
        assert "減倉警報" in text
        assert "BTC" in text
        assert "LONG" in text
        assert "成功執行: 1" in text
        assert "1000" in text

    @pytest.mark.asyncio
    async def test_send_liquidation_alert_dry_run(self, notifier, mock_bot):
        """測試 dry run 模式的減倉警報"""
        plans = [
            LiquidationPlan(
                symbol="ETH",
                side="SHORT",
                current_quantity=Decimal("10"),
                close_quantity=Decimal("2.5"),
                current_price=Decimal("3000"),
                estimated_release=Decimal("750"),
            ),
        ]

        result = LiquidationResult(
            executed=False,
            reason="Dry run mode",
            plans=plans,
        )

        success = await notifier.send_liquidation_alert(result)

        assert success is True
        text = mock_bot.send_message.call_args[1]["text"]
        assert "減倉警報" in text
        assert "ETH" in text
        assert "SHORT" in text
        assert "尚未執行" in text

    @pytest.mark.asyncio
    async def test_send_liquidation_alert_empty(self, notifier, mock_bot):
        """測試沒有減倉計畫時不發送"""
        result = LiquidationResult(
            executed=False,
            reason="No margin gap",
            plans=[],
        )

        success = await notifier.send_liquidation_alert(result)

        assert success is True
        mock_bot.send_message.assert_not_called()


class TestSendDailyReport:
    """測試 send_daily_report 方法"""

    @pytest.mark.asyncio
    async def test_send_daily_report_basic(self, notifier, mock_bot):
        """測試基本每日報告"""
        stats = {
            "adjustment_count": 15,
            "liquidation_count": 2,
        }

        success = await notifier.send_daily_report(stats)

        assert success is True
        text = mock_bot.send_message.call_args[1]["text"]
        assert "每日統計報告" in text
        assert "15" in text
        assert "2" in text

    @pytest.mark.asyncio
    async def test_send_daily_report_with_balance(self, notifier, mock_bot):
        """測試包含餘額的每日報告"""
        stats = {
            "adjustment_count": 10,
            "liquidation_count": 0,
            "total_equity": Decimal("50000.00"),
            "total_margin": Decimal("30000.00"),
            "available_balance": Decimal("20000.00"),
        }

        success = await notifier.send_daily_report(stats)

        assert success is True
        text = mock_bot.send_message.call_args[1]["text"]
        assert "總權益" in text
        assert "總保證金" in text
        assert "可用餘額" in text

    @pytest.mark.asyncio
    async def test_send_daily_report_disabled(self, disabled_notifier):
        """測試停用時不發送"""
        stats = {"adjustment_count": 5, "liquidation_count": 1}
        success = await disabled_notifier.send_daily_report(stats)
        assert success is True


class TestSendApiErrorAlert:
    """測試 send_api_error_alert 方法"""

    @pytest.mark.asyncio
    async def test_send_api_error_alert(self, notifier, mock_bot):
        """測試 API 錯誤警報"""
        error = ConnectionError("Failed to connect to Bitfinex API")

        success = await notifier.send_api_error_alert(error, retry_count=5)

        assert success is True
        text = mock_bot.send_message.call_args[1]["text"]
        assert "API 錯誤警報" in text
        assert "ConnectionError" in text
        assert "Failed to connect" in text
        assert "5" in text

    @pytest.mark.asyncio
    async def test_send_api_error_alert_disabled(self, disabled_notifier):
        """測試停用時不發送"""
        error = Exception("Test error")
        success = await disabled_notifier.send_api_error_alert(error, retry_count=3)
        assert success is True


class TestSendAccountMarginWarning:
    """測試 send_account_margin_warning 方法"""

    @pytest.mark.asyncio
    async def test_send_account_margin_warning(self, notifier, mock_bot):
        """測試帳戶保證金率警告"""
        success = await notifier.send_account_margin_warning(margin_rate=2.5)

        assert success is True
        text = mock_bot.send_message.call_args[1]["text"]
        assert "帳戶保證金率警告" in text
        assert "2.50%" in text
        assert "風險管理" in text

    @pytest.mark.asyncio
    async def test_send_account_margin_warning_disabled(self, disabled_notifier):
        """測試停用時不發送"""
        success = await disabled_notifier.send_account_margin_warning(margin_rate=1.5)
        assert success is True


class TestNotifierDisabled:
    """測試通知器停用狀態"""

    @pytest.mark.asyncio
    async def test_all_methods_noop_when_disabled(self, disabled_notifier):
        """測試停用時所有方法都是 no-op"""
        # send_message
        assert await disabled_notifier.send_message("test") is True

        # send_adjustment_report
        result = RebalanceResult(
            success_count=1,
            fail_count=0,
            total_adjusted=Decimal("100"),
            adjustments=[],
        )
        assert await disabled_notifier.send_adjustment_report(result) is True

        # send_liquidation_alert
        liq_result = LiquidationResult(
            executed=True,
            reason="test",
            plans=[
                LiquidationPlan(
                    symbol="BTC",
                    side="LONG",
                    current_quantity=Decimal("1"),
                    close_quantity=Decimal("0.5"),
                    current_price=Decimal("50000"),
                    estimated_release=Decimal("500"),
                ),
            ],
            success_count=1,
            fail_count=0,
            total_released=Decimal("500"),
        )
        assert await disabled_notifier.send_liquidation_alert(liq_result) is True

        # send_daily_report
        assert await disabled_notifier.send_daily_report({"adjustment_count": 5}) is True

        # send_api_error_alert
        assert await disabled_notifier.send_api_error_alert(Exception("test"), 3) is True

        # send_account_margin_warning
        assert await disabled_notifier.send_account_margin_warning(2.0) is True
