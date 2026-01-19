"""事件偵測模組：監控緊急事件並即時反應"""

import logging
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional

if TYPE_CHECKING:
    from src.config_manager import Config
    from src.core.margin_allocator import MarginAllocator
    from src.notifier.telegram_bot import TelegramNotifier

from src.storage.models import Position

logger = logging.getLogger(__name__)


class EventDetector:
    """事件偵測器：監控緊急事件"""

    def __init__(
        self,
        config: "Config",
        allocator: "MarginAllocator",
        notifier: "TelegramNotifier",
    ):
        """初始化事件偵測器

        Args:
            config: 配置物件
            allocator: 保證金分配器
            notifier: Telegram 通知器
        """
        self.config = config
        self.allocator = allocator
        self.notifier = notifier

        # 價格追蹤快取
        self._price_cache: Dict[str, Decimal] = {}

        # 帳戶保證金率警告狀態（避免重複警告）
        self._margin_warning_sent: bool = False

    def check_emergency_conditions(
        self, positions: List[Position]
    ) -> List[Position]:
        """檢查是否有倉位處於緊急狀態

        當倉位保證金率低於 emergency_margin_rate 時視為緊急

        Args:
            positions: 當前倉位列表

        Returns:
            危險倉位列表（保證金率過低的倉位）
        """
        critical_positions: List[Position] = []
        threshold = self.config.thresholds.emergency_margin_rate

        for pos in positions:
            margin_rate = float(pos.margin_rate)
            if margin_rate < threshold:
                logger.warning(
                    f"Emergency condition detected: {pos.symbol} "
                    f"margin_rate={margin_rate:.2f}% < {threshold}%"
                )
                critical_positions.append(pos)

        return critical_positions

    def on_price_update(
        self,
        symbol: str,
        price: Decimal,
        prev_price: Optional[Decimal] = None,
    ) -> bool:
        """處理價格更新，檢查是否發生價格急漲急跌

        Args:
            symbol: 幣種符號
            price: 當前價格
            prev_price: 前一價格（可選，若未提供則使用快取）

        Returns:
            是否觸發價格急漲急跌警報
        """
        # 如果未提供前一價格，從快取取得
        if prev_price is None:
            prev_price = self._price_cache.get(symbol)

        # 更新快取
        self._price_cache[symbol] = price

        # 如果沒有前一價格可比較，直接返回
        if prev_price is None or prev_price == 0:
            return False

        # 計算價格變動百分比
        price_change_pct = abs(
            (float(price) - float(prev_price)) / float(prev_price) * 100
        )

        threshold = self.config.thresholds.price_spike_pct

        if price_change_pct >= threshold:
            logger.warning(
                f"Price spike detected: {symbol} "
                f"changed {price_change_pct:.2f}% "
                f"({prev_price} -> {price})"
            )
            return True

        return False

    def check_account_margin_rate(
        self,
        total_equity: Decimal,
        total_margin: Decimal,
    ) -> bool:
        """檢查整體帳戶保證金率

        當整體保證金率低於 account_margin_rate_warning 時發送警告

        Args:
            total_equity: 總權益
            total_margin: 總保證金

        Returns:
            是否觸發警告
        """
        if total_margin == 0:
            # 沒有倉位，重置警告狀態
            self._margin_warning_sent = False
            return False

        # 計算帳戶保證金率
        margin_rate = float(total_equity / total_margin * 100)
        threshold = self.config.thresholds.account_margin_rate_warning

        if margin_rate < threshold:
            logger.warning(
                f"Account margin rate warning: {margin_rate:.2f}% < {threshold}%"
            )
            return True

        # 如果已經恢復正常，重置警告狀態
        self._margin_warning_sent = False
        return False

    async def handle_emergency(
        self,
        critical_position: Position,
        positions: List[Position],
        available_balance: Decimal,
    ) -> bool:
        """處理緊急狀態：觸發緊急重平衡並通知

        Args:
            critical_position: 處於危險狀態的倉位
            positions: 所有倉位
            available_balance: 可用餘額

        Returns:
            是否處理成功
        """
        logger.info(
            f"Handling emergency for {critical_position.symbol} "
            f"(margin_rate={critical_position.margin_rate:.2f}%)"
        )

        # 執行緊急重平衡
        result = await self.allocator.emergency_rebalance(
            positions=positions,
            critical_position=critical_position,
            available_balance=available_balance,
        )

        # 發送通知
        if result.success_count > 0:
            await self.notifier.send_adjustment_report(result)
            logger.info(
                f"Emergency rebalance completed: {result.success_count} adjustments"
            )
            return True
        elif result.fail_count > 0:
            logger.error(
                f"Emergency rebalance failed: {result.fail_count} failures"
            )
            return False

        return True

    async def handle_account_margin_warning(
        self, margin_rate: float
    ) -> bool:
        """處理帳戶保證金率警告

        Args:
            margin_rate: 當前帳戶保證金率

        Returns:
            是否發送了警告
        """
        # 避免重複發送警告
        if self._margin_warning_sent:
            return False

        self._margin_warning_sent = True
        await self.notifier.send_account_margin_warning(margin_rate)
        return True

    def get_cached_price(self, symbol: str) -> Optional[Decimal]:
        """取得快取中的價格

        Args:
            symbol: 幣種符號

        Returns:
            快取的價格，若無則回傳 None
        """
        return self._price_cache.get(symbol)

    def clear_price_cache(self) -> None:
        """清除價格快取"""
        self._price_cache.clear()

    def reset_warning_state(self) -> None:
        """重置警告狀態"""
        self._margin_warning_sent = False
