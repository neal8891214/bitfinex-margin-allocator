"""Poll Scheduler 模組：定時執行保證金重平衡"""

import asyncio
import json
import logging
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Optional

if TYPE_CHECKING:
    from src.api.bitfinex_client import BitfinexClient
    from src.config_manager import Config
    from src.core.margin_allocator import MarginAllocator
    from src.core.position_liquidator import PositionLiquidator
    from src.core.risk_calculator import RiskCalculator
    from src.notifier.telegram_bot import TelegramNotifier
    from src.storage.database import Database

from src.storage.models import AccountSnapshot, TriggerType

logger = logging.getLogger(__name__)


class PollScheduler:
    """定時輪詢排程器

    定時執行以下任務：
    1. 取得當前倉位和可用餘額
    2. 執行保證金重平衡
    3. 檢查並執行減倉（如果需要）
    4. 記錄帳戶快照
    5. 發送通知
    """

    def __init__(
        self,
        config: "Config",
        client: "BitfinexClient",
        risk_calculator: "RiskCalculator",
        allocator: "MarginAllocator",
        liquidator: "PositionLiquidator",
        notifier: "TelegramNotifier",
        db: "Database",
    ):
        """初始化排程器

        Args:
            config: 配置物件
            client: Bitfinex API 客戶端
            risk_calculator: 風險計算器
            allocator: 保證金分配器
            liquidator: 減倉執行器
            notifier: Telegram 通知器
            db: 資料庫
        """
        self.config = config
        self.client = client
        self.risk_calculator = risk_calculator
        self.allocator = allocator
        self.liquidator = liquidator
        self.notifier = notifier
        self.db = db

        self._running = False
        self._task: Optional[asyncio.Task[None]] = None

    async def start(self) -> None:
        """開始定時輪詢"""
        if self._running:
            logger.warning("PollScheduler is already running")
            return

        self._running = True
        self._task = asyncio.create_task(self._poll_loop())
        logger.info(
            f"PollScheduler started with interval: "
            f"{self.config.monitor.poll_interval_sec}s"
        )

    async def stop(self) -> None:
        """停止定時輪詢"""
        self._running = False

        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

        logger.info("PollScheduler stopped")

    async def _poll_loop(self) -> None:
        """輪詢迴圈"""
        while self._running:
            try:
                await self.run_once()
            except Exception as e:
                logger.error(f"Error in poll loop: {e}")

            # 等待下一次輪詢
            await asyncio.sleep(self.config.monitor.poll_interval_sec)

    async def run_once(self) -> None:
        """執行單次重平衡流程

        此方法可獨立呼叫，用於測試或手動觸發
        """
        logger.info("Starting rebalance cycle")

        try:
            # 1. 取得當前倉位
            positions = await self.client.get_positions()
            logger.info(f"Retrieved {len(positions)} active positions")

            if not positions:
                logger.info("No active positions, skipping rebalance")
                return

            # 2. 取得可用餘額
            available_balance = await self.client.get_derivatives_balance()
            logger.info(f"Available balance: {available_balance} USDT")

            # 3. 計算總保證金
            total_margin = sum((p.margin for p in positions), Decimal("0"))
            total_available = available_balance + total_margin

            # 4. 執行保證金重平衡
            rebalance_result = await self.allocator.execute_rebalance(
                positions, total_available, TriggerType.SCHEDULED
            )
            logger.info(
                f"Rebalance completed: "
                f"{rebalance_result.success_count} success, "
                f"{rebalance_result.fail_count} failed"
            )

            # 5. 發送調整報告（如果有調整）
            if rebalance_result.success_count > 0 or rebalance_result.fail_count > 0:
                await self.notifier.send_adjustment_report(rebalance_result)

            # 6. 檢查並執行減倉（如果需要）
            # 重新取得餘額（可能因調整而變化）
            updated_balance = await self.client.get_derivatives_balance()
            liquidation_result = await self.liquidator.execute_if_needed(
                positions, updated_balance
            )

            if liquidation_result.executed or liquidation_result.plans:
                logger.info(
                    f"Liquidation check: executed={liquidation_result.executed}, "
                    f"plans={len(liquidation_result.plans)}"
                )
                await self.notifier.send_liquidation_alert(liquidation_result)

            # 7. 記錄帳戶快照
            await self._save_account_snapshot(
                positions, available_balance, total_margin
            )

        except Exception as e:
            logger.error(f"Error during rebalance cycle: {e}")
            raise

    async def _save_account_snapshot(
        self,
        positions: list,
        available_balance: Decimal,
        total_margin: Decimal,
    ) -> None:
        """儲存帳戶快照

        Args:
            positions: 倉位列表
            available_balance: 可用餘額
            total_margin: 總保證金
        """
        total_equity = available_balance + total_margin

        # 將倉位轉換為 JSON 可序列化的格式
        positions_data = []
        for pos in positions:
            positions_data.append({
                "symbol": pos.symbol,
                "side": pos.side.value,
                "quantity": str(pos.quantity),
                "current_price": str(pos.current_price),
                "margin": str(pos.margin),
                "margin_rate": str(pos.margin_rate),
            })

        snapshot = AccountSnapshot(
            timestamp=datetime.now(),
            total_equity=total_equity,
            total_margin=total_margin,
            available_balance=available_balance,
            positions_json=positions_data,
        )

        await self.db.save_account_snapshot(snapshot)
        logger.debug("Account snapshot saved")
