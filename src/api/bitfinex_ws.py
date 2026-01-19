"""Bitfinex WebSocket 客戶端：即時價格更新"""

import asyncio
import json
import logging
from decimal import Decimal
from typing import Any, Callable, Coroutine, Dict, List, Optional, Set, Union

import websockets
from websockets.exceptions import ConnectionClosed

from src.storage.models import Position

logger = logging.getLogger(__name__)

# 回調函數類型：接收 symbol, price
PriceCallback = Callable[[str, Decimal], Coroutine[Any, Any, None]]


class BitfinexWebSocket:
    """Bitfinex WebSocket 客戶端

    提供即時價格更新功能，支援智慧訂閱和自動重連
    """

    MAX_RECONNECT_ATTEMPTS = 10
    INITIAL_RECONNECT_DELAY = 1.0  # 秒

    def __init__(
        self,
        ws_url: str,
        emergency_margin_rate: float = 2.0,
    ):
        """初始化 WebSocket 客戶端

        Args:
            ws_url: WebSocket 連線 URL
            emergency_margin_rate: 緊急保證金率閾值，用於智慧訂閱判斷
        """
        self.ws_url = ws_url
        self.emergency_margin_rate = emergency_margin_rate

        self._ws: Any = None  # websockets.ClientConnection
        self._running: bool = False
        self._reconnect_task: Optional[asyncio.Task[None]] = None
        self._listen_task: Optional[asyncio.Task[None]] = None

        # 訂閱管理
        self._subscribed_symbols: Set[str] = set()
        self._channel_map: Dict[int, str] = {}  # channel_id -> symbol

        # 訊息回調
        self._callbacks: List[PriceCallback] = []

    async def connect(self) -> bool:
        """建立 WebSocket 連線

        Returns:
            是否連線成功
        """
        try:
            self._ws = await websockets.connect(self.ws_url)
            self._running = True
            logger.info(f"WebSocket connected to {self.ws_url}")
            return True
        except Exception as e:
            logger.error(f"WebSocket connection failed: {e}")
            return False

    async def subscribe(self, symbols: List[str]) -> None:
        """訂閱價格更新頻道

        Args:
            symbols: 要訂閱的符號列表（簡短格式，如 "BTC"）
        """
        if self._ws is None or not self._running:
            logger.warning("WebSocket not connected, cannot subscribe")
            return

        for symbol in symbols:
            if symbol in self._subscribed_symbols:
                continue

            # 使用衍生品交易對格式
            full_symbol = f"t{symbol}F0:USTF0"

            # 訂閱 ticker 頻道
            subscribe_msg = {
                "event": "subscribe",
                "channel": "ticker",
                "symbol": full_symbol,
            }

            try:
                await self._ws.send(json.dumps(subscribe_msg))
                self._subscribed_symbols.add(symbol)
                logger.debug(f"Subscribed to {full_symbol}")
            except Exception as e:
                logger.error(f"Failed to subscribe {symbol}: {e}")

    async def unsubscribe(self, symbols: List[str]) -> None:
        """取消訂閱價格更新頻道

        Args:
            symbols: 要取消訂閱的符號列表
        """
        if self._ws is None or not self._running:
            return

        for symbol in symbols:
            if symbol not in self._subscribed_symbols:
                continue

            # 找到對應的 channel_id
            channel_id: Optional[int] = None
            for cid, sym in self._channel_map.items():
                if sym == symbol:
                    channel_id = cid
                    break

            if channel_id is not None:
                unsubscribe_msg = {
                    "event": "unsubscribe",
                    "chanId": channel_id,
                }

                try:
                    await self._ws.send(json.dumps(unsubscribe_msg))
                    self._subscribed_symbols.discard(symbol)
                    del self._channel_map[channel_id]
                    logger.debug(f"Unsubscribed from {symbol}")
                except Exception as e:
                    logger.error(f"Failed to unsubscribe {symbol}: {e}")

    def _is_high_risk(self, position: Position) -> bool:
        """判斷倉位是否為高風險

        高風險定義：保證金率低於 emergency_margin_rate * 2

        Args:
            position: 倉位資料

        Returns:
            是否為高風險
        """
        threshold = self.emergency_margin_rate * 2
        return float(position.margin_rate) < threshold

    async def update_subscriptions(self, positions: List[Position]) -> None:
        """根據當前倉位風險動態調整訂閱列表

        只訂閱高風險倉位（保證金率低於 emergency_margin_rate * 2）

        Args:
            positions: 當前倉位列表
        """
        # 找出需要監控的高風險倉位
        high_risk_symbols: Set[str] = set()
        for pos in positions:
            if self._is_high_risk(pos):
                high_risk_symbols.add(pos.symbol)
                logger.debug(
                    f"High risk position: {pos.symbol} "
                    f"(margin_rate={pos.margin_rate:.2f}%)"
                )

        # 計算需要新增和移除的訂閱
        to_subscribe = high_risk_symbols - self._subscribed_symbols
        to_unsubscribe = self._subscribed_symbols - high_risk_symbols

        # 執行訂閱變更
        if to_unsubscribe:
            await self.unsubscribe(list(to_unsubscribe))
        if to_subscribe:
            await self.subscribe(list(to_subscribe))

        logger.info(
            f"Subscriptions updated: {len(self._subscribed_symbols)} symbols monitored"
        )

    def on_message(self, callback: PriceCallback) -> None:
        """註冊訊息回調函數

        Args:
            callback: 收到價格更新時呼叫的函數，接收 (symbol, price)
        """
        self._callbacks.append(callback)

    def _parse_symbol_from_full(self, full_symbol: str) -> str:
        """從完整符號解析出簡短符號

        Args:
            full_symbol: 完整符號，如 "tBTCF0:USTF0"

        Returns:
            簡短符號，如 "BTC"
        """
        return full_symbol.replace("t", "").split("F0")[0]

    async def _handle_message(self, message: Union[str, bytes]) -> None:
        """處理接收到的 WebSocket 訊息

        Args:
            message: JSON 格式的訊息
        """
        # 將 bytes 轉換為 str
        if isinstance(message, bytes):
            message = message.decode("utf-8")

        try:
            data = json.loads(message)
        except json.JSONDecodeError:
            logger.warning(f"Invalid JSON message: {message}")
            return

        # 處理事件訊息（訂閱確認等）
        if isinstance(data, dict):
            event = data.get("event")

            if event == "subscribed":
                channel_id = data.get("chanId")
                symbol = data.get("symbol", "")
                short_symbol = self._parse_symbol_from_full(symbol)
                if channel_id is not None:
                    self._channel_map[channel_id] = short_symbol
                    logger.info(f"Channel {channel_id} mapped to {short_symbol}")

            elif event == "unsubscribed":
                channel_id = data.get("chanId")
                if channel_id is not None and channel_id in self._channel_map:
                    del self._channel_map[channel_id]

            elif event == "error":
                logger.error(f"WebSocket error: {data.get('msg')}")

            elif event == "info":
                version = data.get("version")
                if version:
                    logger.info(f"WebSocket API version: {version}")

            return

        # 處理頻道資料
        if isinstance(data, list) and len(data) >= 2:
            channel_id = data[0]
            payload = data[1]

            # 忽略心跳
            if payload == "hb":
                return

            # 取得對應的 symbol
            symbol = self._channel_map.get(channel_id)
            if symbol is None:
                return

            # 解析價格資料（ticker 格式）
            # [CHANNEL_ID, [BID, BID_SIZE, ASK, ASK_SIZE, DAILY_CHANGE, DAILY_CHANGE_PERC, LAST_PRICE, VOLUME, HIGH, LOW]]
            if isinstance(payload, list) and len(payload) >= 7:
                last_price = payload[6]  # LAST_PRICE
                if last_price is not None:
                    price = Decimal(str(last_price))

                    # 呼叫所有註冊的回調
                    for callback in self._callbacks:
                        try:
                            await callback(symbol, price)
                        except Exception as e:
                            logger.error(f"Callback error: {e}")

    async def _listen(self) -> None:
        """監聽 WebSocket 訊息"""
        if self._ws is None:
            return

        try:
            async for message in self._ws:
                if not self._running:
                    break
                await self._handle_message(message)
        except ConnectionClosed:
            logger.warning("WebSocket connection closed")
            if self._running:
                await self._reconnect()
        except Exception as e:
            logger.error(f"WebSocket listen error: {e}")
            if self._running:
                await self._reconnect()

    async def start(self) -> None:
        """開始監聽訊息（非阻塞）"""
        if self._listen_task is not None:
            return

        self._listen_task = asyncio.create_task(self._listen())

    async def _reconnect(self) -> None:
        """自動重連機制：斷線後指數退避重連"""
        delay = self.INITIAL_RECONNECT_DELAY

        for attempt in range(self.MAX_RECONNECT_ATTEMPTS):
            if not self._running:
                return

            logger.info(
                f"Reconnecting... attempt {attempt + 1}/{self.MAX_RECONNECT_ATTEMPTS}"
            )

            await asyncio.sleep(delay)

            # 嘗試重新連線
            if await self.connect():
                # 重新訂閱之前的頻道
                symbols_to_resubscribe = list(self._subscribed_symbols)
                self._subscribed_symbols.clear()
                self._channel_map.clear()

                await self.subscribe(symbols_to_resubscribe)
                await self.start()
                logger.info("Reconnected successfully")
                return

            # 指數退避
            delay = min(delay * 2, 60.0)  # 最多等 60 秒

        logger.error(
            f"Failed to reconnect after {self.MAX_RECONNECT_ATTEMPTS} attempts"
        )
        self._running = False

    async def close(self) -> None:
        """關閉 WebSocket 連線"""
        self._running = False

        # 取消監聽任務
        if self._listen_task is not None:
            self._listen_task.cancel()
            try:
                await self._listen_task
            except asyncio.CancelledError:
                pass
            self._listen_task = None

        # 關閉連線
        if self._ws is not None:
            try:
                await self._ws.close()
            except Exception:
                pass
            self._ws = None

        self._subscribed_symbols.clear()
        self._channel_map.clear()
        self._callbacks.clear()

        logger.info("WebSocket closed")

    @property
    def is_connected(self) -> bool:
        """檢查是否已連線"""
        return self._ws is not None and self._running

    @property
    def subscribed_symbols(self) -> Set[str]:
        """取得已訂閱的符號列表"""
        return self._subscribed_symbols.copy()
