"""倉位減倉模組：當保證金不足時自動減倉"""

import time
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, List

if TYPE_CHECKING:
    from src.api.bitfinex_client import BitfinexClient
    from src.config_manager import Config
    from src.storage.database import Database

from src.storage.models import Position, PositionSide, Liquidation


@dataclass
class LiquidationPlan:
    """減倉計畫"""

    symbol: str
    side: str
    current_quantity: Decimal
    close_quantity: Decimal
    current_price: Decimal
    estimated_release: Decimal


@dataclass
class LiquidationResult:
    """減倉結果"""

    executed: bool
    reason: str
    plans: List[LiquidationPlan]
    success_count: int = 0
    fail_count: int = 0
    total_released: Decimal = field(default_factory=lambda: Decimal("0"))


class PositionLiquidator:
    """倉位減倉執行器"""

    MAINTENANCE_MARGIN_RATE = Decimal("0.005")  # 0.5%

    def __init__(
        self,
        config: "Config",
        client: "BitfinexClient",
        db: "Database",
    ):
        self.config = config
        self.client = client
        self.db = db
        self._last_liquidation_time: float = 0

    def _calculate_margin_gap(
        self,
        positions: List[Position],
        available_balance: Decimal,
    ) -> Decimal:
        """計算保證金缺口

        Args:
            positions: 所有倉位
            available_balance: 可用餘額

        Returns:
            保證金缺口（正數表示需要減倉）
        """
        total_notional = sum(pos.notional_value for pos in positions)
        total_margin = sum(pos.margin for pos in positions)

        # 最低安全保證金 = 名義價值 * 維護保證金率 * 安全係數
        min_safe_margin = (
            total_notional
            * self.MAINTENANCE_MARGIN_RATE
            * Decimal(str(self.config.liquidation.safety_margin_multiplier))
        )

        # 缺口 = 最低安全保證金 - 當前總保證金 - 可用餘額
        gap = min_safe_margin - total_margin - available_balance
        return max(gap, Decimal("0"))

    def _sort_by_priority(self, positions: List[Position]) -> List[Position]:
        """按優先級排序（低優先級在前，優先被減倉）

        Args:
            positions: 倉位列表

        Returns:
            排序後的倉位列表
        """
        return sorted(
            positions,
            key=lambda p: self.config.get_position_priority(p.symbol),
        )

    def _create_liquidation_plan(
        self,
        position: Position,
        needed_release: Decimal,
    ) -> LiquidationPlan:
        """建立單一倉位的減倉計畫

        Args:
            position: 倉位
            needed_release: 需要釋放的保證金金額

        Returns:
            減倉計畫
        """
        # 最多平倉比例
        max_close_pct = Decimal(str(self.config.liquidation.max_single_close_pct)) / 100
        max_close_qty = position.quantity * max_close_pct

        # 計算需要平多少才能釋放所需保證金
        if position.margin > 0:
            margin_per_unit = position.margin / position.quantity
            qty_for_release = needed_release / margin_per_unit
        else:
            qty_for_release = Decimal("0")

        # 取較小值
        close_qty = min(max_close_qty, qty_for_release)

        # 估算釋放的保證金
        if position.quantity > 0:
            estimated_release = (close_qty / position.quantity) * position.margin
        else:
            estimated_release = Decimal("0")

        return LiquidationPlan(
            symbol=position.symbol,
            side=position.side.value,
            current_quantity=position.quantity,
            close_quantity=close_qty,
            current_price=position.current_price,
            estimated_release=estimated_release,
        )

    def _check_cooldown(self) -> bool:
        """檢查是否已過冷卻期

        Returns:
            True 表示可以執行，False 表示在冷卻期內
        """
        if self._last_liquidation_time == 0:
            return True

        elapsed = time.time() - self._last_liquidation_time
        return elapsed >= self.config.liquidation.cooldown_seconds

    async def execute_if_needed(
        self,
        positions: List[Position],
        available_balance: Decimal,
    ) -> LiquidationResult:
        """檢查並執行減倉（如果需要）

        Args:
            positions: 所有倉位
            available_balance: 可用餘額

        Returns:
            減倉結果
        """
        # 檢查是否啟用
        if not self.config.liquidation.enabled:
            return LiquidationResult(
                executed=False,
                reason="Liquidation disabled",
                plans=[],
            )

        # 檢查冷卻期
        if not self._check_cooldown():
            return LiquidationResult(
                executed=False,
                reason="In cooldown period",
                plans=[],
            )

        # 計算保證金缺口
        gap = self._calculate_margin_gap(positions, available_balance)

        if gap <= 0:
            return LiquidationResult(
                executed=False,
                reason="No margin gap",
                plans=[],
            )

        # 按優先級排序
        sorted_positions = self._sort_by_priority(positions)

        # 建立減倉計畫
        plans: List[LiquidationPlan] = []
        remaining_gap = gap

        for pos in sorted_positions:
            if remaining_gap <= 0:
                break
            plan = self._create_liquidation_plan(pos, remaining_gap)
            plans.append(plan)
            remaining_gap -= plan.estimated_release

        # dry run 模式
        if self.config.liquidation.dry_run:
            return LiquidationResult(
                executed=False,
                reason="Dry run mode",
                plans=plans,
            )

        # 執行減倉
        success_count = 0
        fail_count = 0
        total_released = Decimal("0")

        for plan in plans:
            full_symbol = self.client.get_full_symbol(plan.symbol)
            side = PositionSide(plan.side)

            success = await self.client.close_position(
                full_symbol,
                side,
                plan.close_quantity,
            )

            if success:
                success_count += 1
                total_released += plan.estimated_release

                # 記錄到資料庫
                liq = Liquidation(
                    timestamp=datetime.now(),
                    symbol=plan.symbol,
                    side=side,
                    quantity=plan.close_quantity,
                    price=plan.current_price,
                    released_margin=plan.estimated_release,
                    reason=f"Margin gap: {gap}",
                )
                await self.db.save_liquidation(liq)
            else:
                fail_count += 1

        # 更新最後執行時間
        self._last_liquidation_time = time.time()

        return LiquidationResult(
            executed=True,
            reason=f"Executed {success_count} liquidations",
            plans=plans,
            success_count=success_count,
            fail_count=fail_count,
            total_released=total_released,
        )
