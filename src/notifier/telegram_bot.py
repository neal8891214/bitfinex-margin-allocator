"""Telegram 通知模組：發送系統通知和警報"""

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Any, Dict

from telegram import Bot
from telegram.error import TelegramError

if TYPE_CHECKING:
    from src.core.margin_allocator import RebalanceResult
    from src.core.position_liquidator import LiquidationResult

logger = logging.getLogger(__name__)


class TelegramNotifier:
    """Telegram 通知器"""

    def __init__(
        self,
        bot_token: str,
        chat_id: str,
        enabled: bool = True,
    ):
        """初始化 Telegram 通知器

        Args:
            bot_token: Telegram Bot Token
            chat_id: 目標聊天 ID
            enabled: 是否啟用通知
        """
        self.bot_token = bot_token
        self.chat_id = chat_id
        self.enabled = enabled
        self._bot: Bot = Bot(token=bot_token)

    async def send_message(self, text: str) -> bool:
        """發送一般訊息

        Args:
            text: 訊息內容

        Returns:
            是否發送成功
        """
        if not self.enabled:
            return True

        try:
            await self._bot.send_message(
                chat_id=self.chat_id,
                text=text,
                parse_mode="HTML",
            )
            return True
        except TelegramError as e:
            logger.error(f"Failed to send Telegram message: {e}")
            return False

    async def send_adjustment_report(
        self, result: "RebalanceResult"
    ) -> bool:
        """發送保證金調整報告

        Args:
            result: 重平衡結果

        Returns:
            是否發送成功
        """
        if not self.enabled:
            return True

        if result.success_count == 0 and result.fail_count == 0:
            return True  # 沒有調整，不發送

        # 建構訊息
        lines = ["<b>📊 保證金調整報告</b>", ""]

        if result.adjustments:
            for adj in result.adjustments:
                direction_emoji = "⬆️" if adj.direction.value == "INCREASE" else "⬇️"
                lines.append(
                    f"{direction_emoji} <b>{adj.symbol}</b>: "
                    f"{adj.before_margin:.2f} → {adj.after_margin:.2f} USDT"
                )

        lines.append("")
        lines.append(f"✅ 成功: {result.success_count}")
        if result.fail_count > 0:
            lines.append(f"❌ 失敗: {result.fail_count}")
        lines.append(f"💰 總調整金額: {result.total_adjusted:.2f} USDT")

        return await self.send_message("\n".join(lines))

    async def send_liquidation_alert(
        self, result: "LiquidationResult"
    ) -> bool:
        """發送減倉警告

        Args:
            result: 減倉結果

        Returns:
            是否發送成功
        """
        if not self.enabled:
            return True

        if not result.executed and not result.plans:
            return True  # 沒有減倉計畫，不發送

        # 建構訊息
        lines = ["<b>🚨 減倉警報</b>", ""]
        lines.append(f"原因: {result.reason}")
        lines.append("")

        if result.plans:
            lines.append("<b>減倉計畫:</b>")
            for plan in result.plans:
                side_emoji = "📈" if plan.side == "LONG" else "📉"
                lines.append(
                    f"{side_emoji} <b>{plan.symbol}</b> ({plan.side}): "
                    f"平倉 {plan.close_quantity:.4f} @ {plan.current_price:.2f}"
                )
                lines.append(
                    f"   預估釋放: {plan.estimated_release:.2f} USDT"
                )

        lines.append("")
        if result.executed:
            lines.append(f"✅ 成功執行: {result.success_count}")
            if result.fail_count > 0:
                lines.append(f"❌ 執行失敗: {result.fail_count}")
            lines.append(f"💰 實際釋放: {result.total_released:.2f} USDT")
        else:
            lines.append("⚠️ 尚未執行（dry run 或冷卻期）")

        return await self.send_message("\n".join(lines))

    async def send_daily_report(self, stats: Dict[str, Any]) -> bool:
        """發送每日統計報告

        Args:
            stats: 統計資料字典，包含 adjustment_count, liquidation_count 等

        Returns:
            是否發送成功
        """
        if not self.enabled:
            return True

        adjustment_count = stats.get("adjustment_count", 0)
        liquidation_count = stats.get("liquidation_count", 0)

        lines = ["<b>📈 每日統計報告</b>", ""]
        lines.append(f"📊 保證金調整次數: {adjustment_count}")
        lines.append(f"🔻 減倉執行次數: {liquidation_count}")

        # 額外的統計資料
        if "total_equity" in stats:
            lines.append("")
            lines.append(f"💰 總權益: {stats['total_equity']:.2f} USDT")
        if "total_margin" in stats:
            lines.append(f"📦 總保證金: {stats['total_margin']:.2f} USDT")
        if "available_balance" in stats:
            lines.append(f"💵 可用餘額: {stats['available_balance']:.2f} USDT")

        return await self.send_message("\n".join(lines))

    async def send_api_error_alert(
        self, error: Exception, retry_count: int
    ) -> bool:
        """發送 API 重試失敗報警

        Args:
            error: 錯誤物件
            retry_count: 已重試次數

        Returns:
            是否發送成功
        """
        if not self.enabled:
            return True

        lines = ["<b>⚠️ API 錯誤警報</b>", ""]
        lines.append(f"錯誤類型: {type(error).__name__}")
        lines.append(f"錯誤訊息: {str(error)}")
        lines.append(f"已重試次數: {retry_count}")
        lines.append("")
        lines.append("請檢查 API 連線狀態和憑證設定。")

        return await self.send_message("\n".join(lines))

    async def send_account_margin_warning(
        self, margin_rate: float
    ) -> bool:
        """發送帳戶保證金率預警

        Args:
            margin_rate: 當前帳戶保證金率（百分比）

        Returns:
            是否發送成功
        """
        if not self.enabled:
            return True

        lines = ["<b>🔴 帳戶保證金率警告</b>", ""]
        lines.append(f"當前帳戶保證金率: <b>{margin_rate:.2f}%</b>")
        lines.append("")
        lines.append("⚠️ 保證金率過低，請注意風險管理！")
        lines.append("建議: 增加保證金或減少倉位")

        return await self.send_message("\n".join(lines))
