"""Bitfinex REST API 客戶端"""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from decimal import Decimal
from typing import Any, Dict, List, Optional

import aiohttp

from src.storage.models import Position, PositionSide

logger = logging.getLogger(__name__)


class BitfinexAPIError(Exception):
    """Bitfinex API 錯誤"""

    def __init__(self, message: str, retry_count: int = 0):
        super().__init__(message)
        self.retry_count = retry_count


class BitfinexClient:
    """Bitfinex API 客戶端"""

    MAX_RETRIES = 10
    BASE_DELAY = 1.0  # 基礎延遲秒數

    def __init__(self, api_key: str, api_secret: str, base_url: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    async def _get_session(self) -> aiohttp.ClientSession:
        """取得或建立 HTTP session"""
        if self._session is None or self._session.closed:
            self._session = aiohttp.ClientSession()
        return self._session

    async def close(self) -> None:
        """關閉 HTTP session"""
        if self._session and not self._session.closed:
            await self._session.close()

    def _generate_signature(self, path: str, nonce: str, body: str) -> str:
        """生成 API 簽名"""
        message = f"/api{path}{nonce}{body}"
        signature = hmac.new(
            self.api_secret.encode("utf-8"),
            message.encode("utf-8"),
            hashlib.sha384,
        ).hexdigest()
        return signature

    async def _request(
        self, method: str, path: str, body: Optional[Dict[str, Any]] = None
    ) -> Any:
        """發送已認證請求（含重試機制）"""
        last_error: Optional[Exception] = None

        for attempt in range(self.MAX_RETRIES):
            try:
                return await self._request_once(method, path, body)
            except aiohttp.ClientError as e:
                last_error = e
                delay = self.BASE_DELAY * (2**attempt)
                logger.warning(
                    f"API request failed (attempt {attempt + 1}/{self.MAX_RETRIES}): {e}"
                )
                await asyncio.sleep(delay)

        raise BitfinexAPIError(
            f"API request failed after {self.MAX_RETRIES} retries: {last_error}",
            retry_count=self.MAX_RETRIES,
        )

    async def _request_once(
        self, method: str, path: str, body: Optional[Dict[str, Any]] = None
    ) -> Any:
        """發送單次已認證請求"""
        session = await self._get_session()
        nonce = str(int(time.time() * 1000000))
        body_json = json.dumps(body) if body else "{}"

        signature = self._generate_signature(path, nonce, body_json)

        headers = {
            "bfx-nonce": nonce,
            "bfx-apikey": self.api_key,
            "bfx-signature": signature,
            "content-type": "application/json",
        }

        url = f"{self.base_url}{path}"

        async with session.request(
            method, url, headers=headers, data=body_json
        ) as response:
            response.raise_for_status()
            return await response.json()

    async def _request_public(self, path: str) -> Any:
        """發送公開請求"""
        session = await self._get_session()
        url = f"{self.base_url}{path}"

        async with session.get(url) as response:
            response.raise_for_status()
            return await response.json()

    def _parse_position(self, raw: List[Any]) -> Position:
        """解析倉位資料

        Bitfinex 衍生品倉位格式:
        [0] SYMBOL, [1] STATUS, [2] AMOUNT, [3] BASE_PRICE,
        [4] MARGIN_FUNDING, [5] MARGIN_FUNDING_TYPE, [6] PL,
        [7] PL_PERC, [8] PRICE_LIQ, [9] LEVERAGE, [10] ID,
        [11] MTS_CREATE, [12] MTS_UPDATE, [13] placeholder,
        [14] TYPE, [15] placeholder, [16] PRICE,
        [17] COLLATERAL, [18] COLLATERAL_MIN, [19] META
        """
        symbol_raw = raw[0]  # e.g., "tBTCF0:USTF0"
        # 提取基礎幣種符號
        symbol = symbol_raw.replace("t", "").split("F0")[0]

        amount = Decimal(str(raw[2]))
        side = PositionSide.LONG if amount > 0 else PositionSide.SHORT
        quantity = abs(amount)

        entry_price = Decimal(str(raw[3]))
        current_price = Decimal(str(raw[16])) if raw[16] else entry_price
        margin = Decimal(str(raw[17])) if raw[17] else Decimal("0")
        leverage = int(raw[9]) if raw[9] else 1
        unrealized_pnl = Decimal(str(raw[6])) if raw[6] else Decimal("0")

        # 計算保證金率
        notional = quantity * current_price
        margin_rate = (margin / notional * 100) if notional > 0 else Decimal("0")

        return Position(
            symbol=symbol,
            side=side,
            quantity=quantity,
            entry_price=entry_price,
            current_price=current_price,
            margin=margin,
            leverage=leverage,
            unrealized_pnl=unrealized_pnl,
            margin_rate=margin_rate,
        )

    async def get_positions(self) -> List[Position]:
        """取得所有衍生品倉位"""
        response = await self._request("POST", "/v2/auth/r/positions")

        positions = []
        for raw in response:
            if raw[1] == "ACTIVE":  # 只處理活躍倉位
                positions.append(self._parse_position(raw))

        return positions

    async def get_derivatives_balance(self) -> Decimal:
        """取得衍生品錢包可用餘額"""
        response = await self._request("POST", "/v2/auth/r/wallets")

        for wallet in response:
            wallet_type = wallet[0]
            currency = wallet[1]
            available = wallet[4]

            if wallet_type == "deriv" and currency in ("UST", "USDt"):
                return Decimal(str(available))

        return Decimal("0")

    async def get_account_info(self) -> Dict[str, Any]:
        """取得帳戶資訊"""
        positions = await self.get_positions()

        total_margin = sum(p.margin for p in positions)
        available = await self.get_derivatives_balance()
        total_equity = available + total_margin

        return {
            "total_equity": total_equity,
            "total_margin": total_margin,
            "available_balance": available,
            "position_count": len(positions),
        }

    async def update_position_margin(self, symbol: str, delta: Decimal) -> bool:
        """更新倉位保證金

        Args:
            symbol: 完整交易對符號，如 "tBTCF0:USTF0"
            delta: 保證金變動量（正數增加，負數減少）
        """
        body = {
            "symbol": symbol,
            "delta": str(delta),
        }

        try:
            response = await self._request(
                "POST", "/v2/auth/w/deriv/collateral/set", body
            )
            # 檢查回應狀態
            if isinstance(response, list) and len(response) > 6:
                status = response[6]
                return status == "SUCCESS"
            return False
        except Exception:
            return False

    async def close_position(
        self,
        symbol: str,
        side: PositionSide,
        quantity: Decimal,
    ) -> bool:
        """市價平倉

        Args:
            symbol: 完整交易對符號
            side: 原倉位方向
            quantity: 平倉數量
        """
        # 平倉方向與持倉相反
        amount = -quantity if side == PositionSide.LONG else quantity

        body = {
            "type": "MARKET",
            "symbol": symbol,
            "amount": str(amount),
            "flags": 0,
        }

        try:
            response = await self._request("POST", "/v2/auth/w/order/submit", body)
            if isinstance(response, list) and len(response) > 6:
                status = response[6]
                return status == "SUCCESS"
            return False
        except Exception:
            return False

    async def get_candles(
        self, symbol: str, timeframe: str = "1D", limit: int = 7
    ) -> List[Dict[str, Any]]:
        """取得 K 線資料

        Args:
            symbol: 交易對符號，如 "tBTCUSD"
            timeframe: 時間框架 (1m, 5m, 15m, 1h, 1D, etc.)
            limit: 取得數量
        """
        path = f"/v2/candles/trade:{timeframe}:{symbol}/hist?limit={limit}"
        response = await self._request_public(path)

        candles = []
        for raw in response:
            candles.append(
                {
                    "timestamp": raw[0],
                    "open": raw[1],
                    "close": raw[2],
                    "high": raw[3],
                    "low": raw[4],
                    "volume": raw[5],
                }
            )

        return candles

    def get_full_symbol(self, symbol: str) -> str:
        """將簡短符號轉換為完整衍生品符號

        Args:
            symbol: 簡短符號，如 "BTC"

        Returns:
            完整符號，如 "tBTCF0:USTF0"
        """
        return f"t{symbol}F0:USTF0"
