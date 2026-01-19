"""風險計算模組：計算波動率與風險權重"""

from decimal import Decimal
from typing import TYPE_CHECKING, Dict, List, Optional

import numpy as np

if TYPE_CHECKING:
    from src.api.bitfinex_client import BitfinexClient
    from src.config_manager import Config

from src.storage.models import Position


class RiskCalculator:
    """風險計算器

    根據波動率計算各幣種的風險權重，用於保證金分配。
    優先使用配置檔的手動權重值，否則自動計算（以 BTC 波動率為基準正規化）。
    """

    def __init__(self, config: "Config", client: "BitfinexClient"):
        self.config = config
        self.client = client
        self._volatility_cache: Dict[str, float] = {}
        self._last_update_time: Optional[float] = None

    def _calculate_volatility(self, prices: List[float]) -> float:
        """計算價格序列的波動率（標準差）

        Args:
            prices: 收盤價列表

        Returns:
            波動率（報酬率的標準差）
        """
        if len(prices) < 2:
            return 1.0  # 預設值

        # 計算報酬率
        price_array = np.array(prices)
        returns = np.diff(price_array) / price_array[:-1]
        volatility = float(np.std(returns))

        # 確保不為零
        return max(volatility, 0.001)

    async def _fetch_volatility(self, symbol: str) -> float:
        """從 API 取得歷史價格並計算波動率

        Args:
            symbol: 幣種符號（如 "BTC"）

        Returns:
            波動率值
        """
        try:
            candles = await self.client.get_candles(
                f"t{symbol}USD",
                "1D",
                self.config.monitor.volatility_lookback_days,
            )

            if not candles:
                return 1.0

            prices = [c["close"] for c in candles]
            return self._calculate_volatility(prices)
        except Exception:
            return 1.0  # 出錯時使用預設值

    async def get_risk_weight(self, symbol: str) -> float:
        """取得幣種的風險權重

        優先使用配置檔的手動值，否則自動計算

        Args:
            symbol: 幣種符號

        Returns:
            風險權重
        """
        # 優先檢查配置
        config_weight = self.config.get_risk_weight(symbol)
        if config_weight is not None:
            return config_weight

        # 檢查快取
        if symbol in self._volatility_cache:
            return self._volatility_cache[symbol]

        # 自動計算
        volatility = await self._fetch_volatility(symbol)

        # 正規化：以 BTC 波動率為基準
        btc_volatility = self._volatility_cache.get("BTC")
        if btc_volatility is None:
            btc_volatility = await self._fetch_volatility("BTC")
            self._volatility_cache["BTC"] = btc_volatility

        # 風險權重 = 該幣種波動率 / BTC 波動率
        weight = volatility / btc_volatility if btc_volatility > 0 else 1.0
        self._volatility_cache[symbol] = weight

        return weight

    async def calculate_target_margins(
        self,
        positions: List[Position],
        total_available_margin: Decimal,
    ) -> Dict[str, Decimal]:
        """計算每個倉位的目標保證金

        公式：目標保證金[i] = 總保證金 × (倉位價值[i] × 風險權重[i]) / Σ(倉位價值 × 風險權重)

        Args:
            positions: 倉位列表
            total_available_margin: 總可用保證金

        Returns:
            幣種 -> 目標保證金 的映射
        """
        if not positions:
            return {}

        # 計算加權值
        weighted_values: Dict[str, Decimal] = {}
        for pos in positions:
            weight = await self.get_risk_weight(pos.symbol)
            weighted_value = pos.notional_value * Decimal(str(weight))
            weighted_values[pos.symbol] = weighted_value

        # 計算總加權值
        total_weighted = sum(weighted_values.values())

        if total_weighted == 0:
            # 平均分配
            avg = total_available_margin / len(positions)
            return {pos.symbol: avg for pos in positions}

        # 計算目標保證金
        targets: Dict[str, Decimal] = {}
        for pos in positions:
            ratio = weighted_values[pos.symbol] / total_weighted
            targets[pos.symbol] = total_available_margin * ratio

        return targets

    def clear_cache(self) -> None:
        """清除波動率快取"""
        self._volatility_cache.clear()
        self._last_update_time = None
