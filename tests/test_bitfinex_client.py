"""Bitfinex REST API Client 測試"""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, patch

from src.api.bitfinex_client import BitfinexClient, BitfinexAPIError
from src.storage.models import PositionSide


@pytest.fixture
def client():
    """建立測試用 client"""
    return BitfinexClient(
        api_key="test_key",
        api_secret="test_secret",
        base_url="https://api.bitfinex.com",
    )


def test_generate_signature(client):
    """測試簽名生成"""
    nonce = "1234567890"
    body = '{"test": "data"}'
    path = "/v2/auth/r/positions"

    signature = client._generate_signature(path, nonce, body)

    assert signature is not None
    assert len(signature) == 96  # SHA384 hex length


def test_parse_position():
    """測試解析倉位資料"""
    # Bitfinex 衍生品倉位格式
    raw = [
        "tBTCF0:USTF0",  # symbol
        "ACTIVE",  # status
        0.5,  # amount (positive = long)
        50000,  # base price
        0,  # margin funding
        0,  # margin funding type
        500,  # pl
        100,  # pl %
        0,  # price (liquidation)
        10,  # leverage
        0,  # id
        1234567890,  # mts_create
        1234567891,  # mts_update
        None,  # placeholder
        0,  # type
        None,  # placeholder
        51000,  # current price
        400,  # collateral (margin)
        0,  # collateral min
        {"meta": "data"},  # meta
    ]

    client = BitfinexClient("k", "s", "url")
    position = client._parse_position(raw)

    assert position.symbol == "BTC"
    assert position.side == PositionSide.LONG
    assert position.quantity == Decimal("0.5")
    assert position.margin == Decimal("400")


def test_parse_position_short():
    """測試解析 Short 倉位"""
    raw = [
        "tETHF0:USTF0",
        "ACTIVE",
        -10,  # negative = short
        3000,
        0,
        0,
        1000,
        50,
        0,
        10,
        0,
        0,
        0,
        None,
        0,
        None,
        2900,
        300,
        0,
        {},
    ]

    client = BitfinexClient("k", "s", "url")
    position = client._parse_position(raw)

    assert position.symbol == "ETH"
    assert position.side == PositionSide.SHORT
    assert position.quantity == Decimal("10")  # quantity 永遠為正


@pytest.mark.asyncio
async def test_get_positions(client):
    """測試取得倉位列表"""
    mock_response = [
        [
            "tBTCF0:USTF0",
            "ACTIVE",
            0.5,
            50000,
            0,
            0,
            500,
            100,
            0,
            10,
            0,
            0,
            0,
            None,
            0,
            None,
            51000,
            400,
            0,
            {},
        ],
    ]

    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_response
        positions = await client.get_positions()

    assert len(positions) == 1
    assert positions[0].symbol == "BTC"


@pytest.mark.asyncio
async def test_get_wallet_balance(client):
    """測試取得錢包餘額"""
    mock_response = [
        ["deriv", "UST", 10000, 0, 9000, None, None],  # derivatives wallet
        ["exchange", "UST", 5000, 0, 5000, None, None],  # exchange wallet
    ]

    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_response
        balance = await client.get_derivatives_balance()

    assert balance == Decimal("9000")  # available balance


@pytest.mark.asyncio
async def test_update_position_margin(client):
    """測試更新倉位保證金"""
    mock_response = [
        1234567890,  # mts
        "miu",  # type
        None,  # message id
        None,  # placeholder
        [
            0,  # id
            "tBTCF0:USTF0",  # symbol
            1,  # type
            100,  # amount
            None,  # placeholder
            None,  # placeholder
            None,  # placeholder
            "SUCCESS",  # status
            None,  # placeholder
        ],
        None,  # code
        "SUCCESS",  # status
        "Margin updated",  # text
    ]

    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_response
        result = await client.update_position_margin("tBTCF0:USTF0", Decimal("100"))

    assert result is True
    mock_request.assert_called_once()


@pytest.mark.asyncio
async def test_get_candles(client):
    """測試取得 K 線資料"""
    mock_response = [
        [1705660800000, 51000, 51500, 50500, 51200, 1000],
        [1705574400000, 50000, 51000, 49500, 50800, 1200],
    ]

    with patch.object(client, "_request_public", new_callable=AsyncMock) as mock:
        mock.return_value = mock_response
        candles = await client.get_candles("tBTCUSD", "1D", limit=2)

    assert len(candles) == 2
    assert candles[0]["close"] == 51500


@pytest.mark.asyncio
async def test_close_position(client):
    """測試平倉"""
    mock_response = [
        1234567890,
        "on-req",
        None,
        None,
        [
            [
                12345,  # order id
                None,
                None,
                "tBTCF0:USTF0",
                None,
                None,
                -0.125,  # amount (negative = sell)
                None,
                "MARKET",
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                51000,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                None,
                {},
            ]
        ],
        None,
        "SUCCESS",
        "Order submitted",
    ]

    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_response
        result = await client.close_position(
            symbol="tBTCF0:USTF0",
            side=PositionSide.LONG,
            quantity=Decimal("0.125"),
        )

    assert result is True


def test_get_full_symbol(client):
    """測試符號轉換"""
    assert client.get_full_symbol("BTC") == "tBTCF0:USTF0"
    assert client.get_full_symbol("ETH") == "tETHF0:USTF0"


@pytest.mark.asyncio
async def test_get_derivatives_balance_usdt(client):
    """測試取得 USDt 餘額"""
    mock_response = [
        ["deriv", "USDt", 5000, 0, 4500, None, None],  # derivatives wallet with USDt
    ]

    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_response
        balance = await client.get_derivatives_balance()

    assert balance == Decimal("4500")


@pytest.mark.asyncio
async def test_get_derivatives_balance_not_found(client):
    """測試找不到衍生品錢包時回傳 0"""
    mock_response = [
        ["exchange", "UST", 5000, 0, 5000, None, None],  # only exchange wallet
    ]

    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_response
        balance = await client.get_derivatives_balance()

    assert balance == Decimal("0")


@pytest.mark.asyncio
async def test_get_positions_filter_inactive(client):
    """測試只取得 ACTIVE 倉位"""
    mock_response = [
        [
            "tBTCF0:USTF0",
            "ACTIVE",
            0.5,
            50000,
            0,
            0,
            500,
            100,
            0,
            10,
            0,
            0,
            0,
            None,
            0,
            None,
            51000,
            400,
            0,
            {},
        ],
        [
            "tETHF0:USTF0",
            "CLOSED",  # 已關閉的倉位
            0,
            3000,
            0,
            0,
            0,
            0,
            0,
            10,
            0,
            0,
            0,
            None,
            0,
            None,
            3000,
            0,
            0,
            {},
        ],
    ]

    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_response
        positions = await client.get_positions()

    assert len(positions) == 1
    assert positions[0].symbol == "BTC"


@pytest.mark.asyncio
async def test_update_position_margin_failure(client):
    """測試更新保證金失敗"""
    mock_response = [
        1234567890,
        "error",
        None,
        None,
        None,
        None,
        "ERROR",
        "Insufficient balance",
    ]

    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.return_value = mock_response
        result = await client.update_position_margin("tBTCF0:USTF0", Decimal("100"))

    assert result is False


@pytest.mark.asyncio
async def test_close_position_failure(client):
    """測試平倉失敗"""
    with patch.object(client, "_request", new_callable=AsyncMock) as mock_request:
        mock_request.side_effect = Exception("Network error")
        result = await client.close_position(
            symbol="tBTCF0:USTF0",
            side=PositionSide.LONG,
            quantity=Decimal("0.125"),
        )

    assert result is False
