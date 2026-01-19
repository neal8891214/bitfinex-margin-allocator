"""Bitfinex WebSocket Client 測試"""

import asyncio
import json
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.api.bitfinex_ws import BitfinexWebSocket
from src.storage.models import Position, PositionSide


@pytest.fixture
def ws_client():
    """建立測試用 WebSocket client"""
    return BitfinexWebSocket(
        ws_url="wss://api.bitfinex.com/ws/2",
        emergency_margin_rate=2.0,
    )


@pytest.fixture
def mock_position_btc():
    """建立 BTC 測試倉位（高風險）"""
    return Position(
        symbol="BTC",
        side=PositionSide.LONG,
        quantity=Decimal("0.5"),
        entry_price=Decimal("50000"),
        current_price=Decimal("51000"),
        margin=Decimal("200"),  # margin_rate ~ 0.78%
        leverage=10,
        unrealized_pnl=Decimal("500"),
        margin_rate=Decimal("0.78"),  # 低於 2% * 2 = 4%，屬於高風險
    )


@pytest.fixture
def mock_position_eth():
    """建立 ETH 測試倉位（低風險）"""
    return Position(
        symbol="ETH",
        side=PositionSide.LONG,
        quantity=Decimal("10"),
        entry_price=Decimal("3000"),
        current_price=Decimal("3100"),
        margin=Decimal("3100"),  # margin_rate ~ 10%
        leverage=10,
        unrealized_pnl=Decimal("1000"),
        margin_rate=Decimal("10.0"),  # 高於 4%，屬於低風險
    )


def test_init(ws_client):
    """測試初始化"""
    assert ws_client.ws_url == "wss://api.bitfinex.com/ws/2"
    assert ws_client.emergency_margin_rate == 2.0
    assert ws_client._ws is None
    assert ws_client._running is False


def test_is_high_risk(ws_client, mock_position_btc, mock_position_eth):
    """測試高風險判斷"""
    # BTC margin_rate = 0.78% < 4%（高風險）
    assert ws_client._is_high_risk(mock_position_btc) is True

    # ETH margin_rate = 10% > 4%（低風險）
    assert ws_client._is_high_risk(mock_position_eth) is False


def test_is_high_risk_boundary(ws_client):
    """測試高風險邊界條件"""
    # 剛好等於閾值（4%）
    pos_at_threshold = Position(
        symbol="SOL",
        side=PositionSide.LONG,
        quantity=Decimal("100"),
        entry_price=Decimal("100"),
        current_price=Decimal("100"),
        margin=Decimal("400"),
        leverage=10,
        unrealized_pnl=Decimal("0"),
        margin_rate=Decimal("4.0"),
    )
    # 4% 不小於 4%，所以不是高風險
    assert ws_client._is_high_risk(pos_at_threshold) is False

    # 剛好低於閾值
    pos_below_threshold = Position(
        symbol="SOL",
        side=PositionSide.LONG,
        quantity=Decimal("100"),
        entry_price=Decimal("100"),
        current_price=Decimal("100"),
        margin=Decimal("399"),
        leverage=10,
        unrealized_pnl=Decimal("0"),
        margin_rate=Decimal("3.99"),
    )
    assert ws_client._is_high_risk(pos_below_threshold) is True


def test_parse_symbol_from_full(ws_client):
    """測試符號解析"""
    assert ws_client._parse_symbol_from_full("tBTCF0:USTF0") == "BTC"
    assert ws_client._parse_symbol_from_full("tETHF0:USTF0") == "ETH"
    assert ws_client._parse_symbol_from_full("tSOLF0:USTF0") == "SOL"


@pytest.mark.asyncio
async def test_connect_success(ws_client):
    """測試連線成功"""
    mock_ws = AsyncMock()

    with patch("src.api.bitfinex_ws.websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_connect.return_value = mock_ws
        result = await ws_client.connect()

    assert result is True
    assert ws_client._running is True
    assert ws_client._ws is mock_ws


@pytest.mark.asyncio
async def test_connect_failure(ws_client):
    """測試連線失敗"""
    with patch("src.api.bitfinex_ws.websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_connect.side_effect = Exception("Connection refused")
        result = await ws_client.connect()

    assert result is False
    assert ws_client._running is False


@pytest.mark.asyncio
async def test_subscribe(ws_client):
    """測試訂閱"""
    mock_ws = AsyncMock()
    ws_client._ws = mock_ws
    ws_client._running = True

    await ws_client.subscribe(["BTC", "ETH"])

    assert mock_ws.send.call_count == 2
    assert "BTC" in ws_client._subscribed_symbols
    assert "ETH" in ws_client._subscribed_symbols


@pytest.mark.asyncio
async def test_subscribe_duplicate(ws_client):
    """測試重複訂閱被忽略"""
    mock_ws = AsyncMock()
    ws_client._ws = mock_ws
    ws_client._running = True
    ws_client._subscribed_symbols.add("BTC")

    await ws_client.subscribe(["BTC", "ETH"])

    # BTC 已訂閱，只發送 ETH 的訂閱
    assert mock_ws.send.call_count == 1


@pytest.mark.asyncio
async def test_subscribe_not_connected(ws_client):
    """測試未連線時訂閱"""
    await ws_client.subscribe(["BTC"])

    # 未連線時不會訂閱
    assert len(ws_client._subscribed_symbols) == 0


@pytest.mark.asyncio
async def test_unsubscribe(ws_client):
    """測試取消訂閱"""
    mock_ws = AsyncMock()
    ws_client._ws = mock_ws
    ws_client._running = True
    ws_client._subscribed_symbols.add("BTC")
    ws_client._channel_map[123] = "BTC"

    await ws_client.unsubscribe(["BTC"])

    mock_ws.send.assert_called_once()
    assert "BTC" not in ws_client._subscribed_symbols
    assert 123 not in ws_client._channel_map


@pytest.mark.asyncio
async def test_update_subscriptions(ws_client, mock_position_btc, mock_position_eth):
    """測試智慧訂閱更新"""
    mock_ws = AsyncMock()
    ws_client._ws = mock_ws
    ws_client._running = True

    positions = [mock_position_btc, mock_position_eth]
    await ws_client.update_subscriptions(positions)

    # 只有高風險的 BTC 應該被訂閱
    assert "BTC" in ws_client._subscribed_symbols
    assert "ETH" not in ws_client._subscribed_symbols


@pytest.mark.asyncio
async def test_update_subscriptions_remove_recovered(ws_client, mock_position_eth):
    """測試倉位恢復後取消訂閱"""
    mock_ws = AsyncMock()
    ws_client._ws = mock_ws
    ws_client._running = True
    ws_client._subscribed_symbols.add("BTC")
    ws_client._subscribed_symbols.add("ETH")
    ws_client._channel_map[123] = "BTC"
    ws_client._channel_map[456] = "ETH"

    # 現在只有 ETH 倉位（低風險）
    positions = [mock_position_eth]
    await ws_client.update_subscriptions(positions)

    # BTC 和 ETH 都應該被取消訂閱（ETH 是低風險）
    assert "BTC" not in ws_client._subscribed_symbols
    assert "ETH" not in ws_client._subscribed_symbols


def test_on_message_callback(ws_client):
    """測試註冊回調"""
    async def callback(symbol: str, price: Decimal) -> None:
        pass

    ws_client.on_message(callback)

    assert len(ws_client._callbacks) == 1


@pytest.mark.asyncio
async def test_handle_message_subscribed(ws_client):
    """測試處理訂閱確認訊息"""
    message = json.dumps({
        "event": "subscribed",
        "channel": "ticker",
        "chanId": 123,
        "symbol": "tBTCF0:USTF0",
    })

    await ws_client._handle_message(message)

    assert ws_client._channel_map[123] == "BTC"


@pytest.mark.asyncio
async def test_handle_message_unsubscribed(ws_client):
    """測試處理取消訂閱確認訊息"""
    ws_client._channel_map[123] = "BTC"

    message = json.dumps({
        "event": "unsubscribed",
        "chanId": 123,
    })

    await ws_client._handle_message(message)

    assert 123 not in ws_client._channel_map


@pytest.mark.asyncio
async def test_handle_message_info(ws_client):
    """測試處理 info 訊息"""
    message = json.dumps({
        "event": "info",
        "version": 2,
    })

    # 應該不會拋出錯誤
    await ws_client._handle_message(message)


@pytest.mark.asyncio
async def test_handle_message_error(ws_client):
    """測試處理錯誤訊息"""
    message = json.dumps({
        "event": "error",
        "msg": "Invalid request",
        "code": 10000,
    })

    # 應該不會拋出錯誤
    await ws_client._handle_message(message)


@pytest.mark.asyncio
async def test_handle_message_heartbeat(ws_client):
    """測試處理心跳訊息"""
    ws_client._channel_map[123] = "BTC"

    message = json.dumps([123, "hb"])

    # 心跳應該被忽略，不觸發回調
    callback = AsyncMock()
    ws_client.on_message(callback)

    await ws_client._handle_message(message)

    callback.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_ticker(ws_client):
    """測試處理 ticker 資料"""
    ws_client._channel_map[123] = "BTC"

    # Ticker 格式: [CHANNEL_ID, [BID, BID_SIZE, ASK, ASK_SIZE, DAILY_CHANGE, DAILY_CHANGE_PERC, LAST_PRICE, VOLUME, HIGH, LOW]]
    message = json.dumps([
        123,
        [50000, 1, 50001, 1, 100, 0.2, 50500, 1000, 51000, 49000]
    ])

    callback = AsyncMock()
    ws_client.on_message(callback)

    await ws_client._handle_message(message)

    callback.assert_called_once_with("BTC", Decimal("50500"))


@pytest.mark.asyncio
async def test_handle_message_ticker_unknown_channel(ws_client):
    """測試處理未知頻道的 ticker 資料"""
    # 沒有設定 channel_map

    message = json.dumps([
        999,
        [50000, 1, 50001, 1, 100, 0.2, 50500, 1000, 51000, 49000]
    ])

    callback = AsyncMock()
    ws_client.on_message(callback)

    await ws_client._handle_message(message)

    # 未知頻道不應該觸發回調
    callback.assert_not_called()


@pytest.mark.asyncio
async def test_handle_message_invalid_json(ws_client):
    """測試處理無效 JSON"""
    message = "invalid json{"

    # 應該不會拋出錯誤
    await ws_client._handle_message(message)


@pytest.mark.asyncio
async def test_close(ws_client):
    """測試關閉連線"""
    mock_ws = AsyncMock()
    ws_client._ws = mock_ws
    ws_client._running = True
    ws_client._subscribed_symbols.add("BTC")
    ws_client._channel_map[123] = "BTC"
    ws_client._callbacks.append(AsyncMock())

    await ws_client.close()

    assert ws_client._running is False
    assert ws_client._ws is None
    assert len(ws_client._subscribed_symbols) == 0
    assert len(ws_client._channel_map) == 0
    assert len(ws_client._callbacks) == 0
    mock_ws.close.assert_called_once()


@pytest.mark.asyncio
async def test_close_with_listen_task(ws_client):
    """測試關閉連線時取消監聽任務"""
    mock_ws = AsyncMock()
    ws_client._ws = mock_ws
    ws_client._running = True

    # 模擬一個正在執行的監聽任務
    async def mock_listen():
        while ws_client._running:
            await asyncio.sleep(0.1)

    ws_client._listen_task = asyncio.create_task(mock_listen())

    await ws_client.close()

    assert ws_client._listen_task is None


def test_is_connected(ws_client):
    """測試連線狀態檢查"""
    assert ws_client.is_connected is False

    ws_client._ws = MagicMock()
    assert ws_client.is_connected is False

    ws_client._running = True
    assert ws_client.is_connected is True


def test_subscribed_symbols(ws_client):
    """測試取得已訂閱符號"""
    ws_client._subscribed_symbols.add("BTC")
    ws_client._subscribed_symbols.add("ETH")

    symbols = ws_client.subscribed_symbols

    assert symbols == {"BTC", "ETH"}
    # 確認是複製而非原始集合
    symbols.add("SOL")
    assert "SOL" not in ws_client._subscribed_symbols


@pytest.mark.asyncio
async def test_callback_error_handling(ws_client):
    """測試回調錯誤處理"""
    ws_client._channel_map[123] = "BTC"

    # 第一個回調會拋出錯誤
    error_callback = AsyncMock(side_effect=Exception("Callback error"))
    # 第二個回調正常
    normal_callback = AsyncMock()

    ws_client.on_message(error_callback)
    ws_client.on_message(normal_callback)

    message = json.dumps([
        123,
        [50000, 1, 50001, 1, 100, 0.2, 50500, 1000, 51000, 49000]
    ])

    await ws_client._handle_message(message)

    # 即使第一個回調錯誤，第二個也應該被呼叫
    error_callback.assert_called_once()
    normal_callback.assert_called_once()


@pytest.mark.asyncio
async def test_reconnect_resubscribes(ws_client):
    """測試重連後重新訂閱"""
    # 設定初始狀態
    ws_client._subscribed_symbols = {"BTC", "ETH"}
    ws_client._running = True

    mock_ws = AsyncMock()

    with patch("src.api.bitfinex_ws.websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_connect.return_value = mock_ws

        # 執行重連
        await ws_client._reconnect()

    # 確認重新訂閱了之前的符號
    assert "BTC" in ws_client._subscribed_symbols
    assert "ETH" in ws_client._subscribed_symbols


@pytest.mark.asyncio
async def test_reconnect_stops_when_not_running(ws_client):
    """測試停止時不重連"""
    ws_client._running = False

    with patch("src.api.bitfinex_ws.websockets.connect", new_callable=AsyncMock) as mock_connect:
        await ws_client._reconnect()

    # 不應該嘗試連線
    mock_connect.assert_not_called()


@pytest.mark.asyncio
async def test_reconnect_max_attempts(ws_client):
    """測試重連最大嘗試次數"""
    ws_client._running = True

    with patch("src.api.bitfinex_ws.websockets.connect", new_callable=AsyncMock) as mock_connect:
        mock_connect.side_effect = Exception("Connection failed")
        with patch("asyncio.sleep", new_callable=AsyncMock):
            await ws_client._reconnect()

    # 應該嘗試 MAX_RECONNECT_ATTEMPTS 次
    assert mock_connect.call_count == ws_client.MAX_RECONNECT_ATTEMPTS
    # 最終應該停止
    assert ws_client._running is False
