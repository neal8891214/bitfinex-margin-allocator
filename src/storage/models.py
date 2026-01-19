"""資料模型定義"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any, List, Dict, Optional

from pydantic import BaseModel, computed_field


class PositionSide(str, Enum):
    """倉位方向"""
    LONG = "long"
    SHORT = "short"


class AdjustmentDirection(str, Enum):
    """調整方向"""
    INCREASE = "increase"
    DECREASE = "decrease"


class TriggerType(str, Enum):
    """觸發類型"""
    SCHEDULED = "scheduled"
    EMERGENCY = "emergency"


class Position(BaseModel):
    """倉位資料模型"""

    symbol: str
    side: PositionSide
    quantity: Decimal
    entry_price: Decimal
    current_price: Decimal
    margin: Decimal
    leverage: int
    unrealized_pnl: Decimal
    margin_rate: Decimal  # 保證金率 (%)

    @computed_field  # type: ignore[prop-decorator]
    @property
    def notional_value(self) -> Decimal:
        """名義價值 = 數量 × 當前價格"""
        return self.quantity * self.current_price

    @computed_field  # type: ignore[prop-decorator]
    @property
    def is_profitable(self) -> bool:
        """是否獲利"""
        return self.unrealized_pnl > 0


class MarginAdjustment(BaseModel):
    """保證金調整記錄"""

    id: Optional[int] = None
    timestamp: datetime
    symbol: str
    direction: AdjustmentDirection
    amount: Decimal
    before_margin: Decimal
    after_margin: Decimal
    trigger_type: TriggerType


class Liquidation(BaseModel):
    """減倉記錄"""

    id: Optional[int] = None
    timestamp: datetime
    symbol: str
    side: PositionSide
    quantity: Decimal
    price: Decimal
    released_margin: Decimal
    reason: str


class AccountSnapshot(BaseModel):
    """帳戶快照"""

    id: Optional[int] = None
    timestamp: datetime
    total_equity: Decimal
    total_margin: Decimal
    available_balance: Decimal
    positions_json: List[Dict[str, Any]]
