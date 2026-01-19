"""保證金分配模組：計算並執行保證金重分配"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List

if TYPE_CHECKING:
    from src.api.bitfinex_client import BitfinexClient
    from src.config_manager import Config
    from src.core.risk_calculator import RiskCalculator
    from src.storage.database import Database

from src.storage.models import (
    Position,
    MarginAdjustment,
    AdjustmentDirection,
    TriggerType,
)


@dataclass
class MarginAdjustmentPlan:
    """保證金調整計畫"""

    symbol: str
    current_margin: Decimal
    target_margin: Decimal
    delta: Decimal

    @property
    def is_increase(self) -> bool:
        """是否為增加保證金"""
        return self.delta > 0


@dataclass
class RebalanceResult:
    """重平衡結果"""

    success_count: int
    fail_count: int
    total_adjusted: Decimal
    adjustments: List[MarginAdjustment]


class MarginAllocator:
    """保證金分配器"""

    def __init__(
        self,
        config: "Config",
        risk_calculator: "RiskCalculator",
        client: "BitfinexClient",
        db: "Database",
    ):
        self.config = config
        self.risk_calculator = risk_calculator
        self.client = client
        self.db = db

    def _calculate_adjustment_plans(
        self,
        positions: List[Position],
        targets: Dict[str, Decimal],
    ) -> List[MarginAdjustmentPlan]:
        """計算調整計畫

        Args:
            positions: 當前倉位列表
            targets: 目標保證金映射

        Returns:
            需要調整的計畫列表（已過濾低於閾值的）
        """
        plans = []

        for pos in positions:
            target = targets.get(pos.symbol)
            if target is None:
                continue

            delta = target - pos.margin
            abs_delta = abs(delta)

            # 檢查是否超過絕對金額閾值
            if abs_delta < self.config.thresholds.min_adjustment_usdt:
                continue

            # 檢查百分比閾值
            if pos.margin > 0:
                pct_deviation = (abs_delta / pos.margin) * 100
                if pct_deviation < self.config.thresholds.min_deviation_pct:
                    continue

            plans.append(
                MarginAdjustmentPlan(
                    symbol=pos.symbol,
                    current_margin=pos.margin,
                    target_margin=target,
                    delta=delta,
                )
            )

        return plans

    def _sort_plans(
        self, plans: List[MarginAdjustmentPlan]
    ) -> List[MarginAdjustmentPlan]:
        """排序調整計畫：先減少（釋放資金），再增加（使用資金）

        Args:
            plans: 調整計畫列表

        Returns:
            排序後的計畫列表
        """
        # 分離增加和減少
        decreases = [p for p in plans if not p.is_increase]
        increases = [p for p in plans if p.is_increase]

        # 減少的按 delta 絕對值從大到小排序
        decreases.sort(key=lambda p: abs(p.delta), reverse=True)
        # 增加的按 delta 從小到大排序
        increases.sort(key=lambda p: p.delta)

        return decreases + increases

    async def execute_rebalance(
        self,
        positions: List[Position],
        total_available_margin: Decimal,
        trigger_type: TriggerType = TriggerType.SCHEDULED,
    ) -> RebalanceResult:
        """執行保證金重平衡

        Args:
            positions: 當前倉位列表
            total_available_margin: 總可用保證金
            trigger_type: 觸發類型

        Returns:
            重平衡結果
        """
        # 計算目標保證金
        targets = await self.risk_calculator.calculate_target_margins(
            positions, total_available_margin
        )

        # 計算調整計畫
        plans = self._calculate_adjustment_plans(positions, targets)

        if not plans:
            return RebalanceResult(
                success_count=0,
                fail_count=0,
                total_adjusted=Decimal("0"),
                adjustments=[],
            )

        # 排序：先減少再增加
        sorted_plans = self._sort_plans(plans)

        # 執行調整
        success_count = 0
        fail_count = 0
        total_adjusted = Decimal("0")
        adjustments: List[MarginAdjustment] = []

        for plan in sorted_plans:
            full_symbol = self.client.get_full_symbol(plan.symbol)
            success = await self.client.update_position_margin(
                full_symbol, plan.delta
            )

            if success:
                success_count += 1
                total_adjusted += abs(plan.delta)

                # 記錄調整
                adj = MarginAdjustment(
                    timestamp=datetime.now(),
                    symbol=plan.symbol,
                    direction=(
                        AdjustmentDirection.INCREASE
                        if plan.is_increase
                        else AdjustmentDirection.DECREASE
                    ),
                    amount=abs(plan.delta),
                    before_margin=plan.current_margin,
                    after_margin=plan.target_margin,
                    trigger_type=trigger_type,
                )
                adjustments.append(adj)

                # 存入資料庫
                await self.db.save_margin_adjustment(adj)
            else:
                fail_count += 1

        return RebalanceResult(
            success_count=success_count,
            fail_count=fail_count,
            total_adjusted=total_adjusted,
            adjustments=adjustments,
        )

    async def emergency_rebalance(
        self,
        positions: List[Position],
        critical_position: Position,
        available_balance: Decimal,
    ) -> RebalanceResult:
        """緊急重平衡：當某倉位保證金率過低時

        Args:
            positions: 所有倉位
            critical_position: 需要緊急補充的倉位
            available_balance: 可用餘額

        Returns:
            重平衡結果
        """
        # 計算需要多少保證金才能達到安全水平
        # 目標：將保證金率提升到 emergency_margin_rate 的 2 倍
        target_rate = self.config.thresholds.emergency_margin_rate * 2
        current_rate = float(critical_position.margin_rate)

        if current_rate >= target_rate:
            return RebalanceResult(
                success_count=0,
                fail_count=0,
                total_adjusted=Decimal("0"),
                adjustments=[],
            )

        # 計算需要增加多少保證金
        notional = critical_position.notional_value
        needed_margin = notional * Decimal(str(target_rate / 100))
        delta = needed_margin - critical_position.margin

        # 限制不超過可用餘額
        delta = min(delta, available_balance)

        if delta < self.config.thresholds.min_adjustment_usdt:
            return RebalanceResult(
                success_count=0,
                fail_count=0,
                total_adjusted=Decimal("0"),
                adjustments=[],
            )

        # 執行緊急調整
        full_symbol = self.client.get_full_symbol(critical_position.symbol)
        success = await self.client.update_position_margin(full_symbol, delta)

        if success:
            adj = MarginAdjustment(
                timestamp=datetime.now(),
                symbol=critical_position.symbol,
                direction=AdjustmentDirection.INCREASE,
                amount=delta,
                before_margin=critical_position.margin,
                after_margin=critical_position.margin + delta,
                trigger_type=TriggerType.EMERGENCY,
            )

            await self.db.save_margin_adjustment(adj)

            return RebalanceResult(
                success_count=1,
                fail_count=0,
                total_adjusted=delta,
                adjustments=[adj],
            )

        return RebalanceResult(
            success_count=0,
            fail_count=1,
            total_adjusted=Decimal("0"),
            adjustments=[],
        )
