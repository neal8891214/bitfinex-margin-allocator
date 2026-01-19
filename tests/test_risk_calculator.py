"""RiskCalculator 模組測試"""

import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

from src.core.risk_calculator import RiskCalculator
from src.storage.models import Position, PositionSide


@pytest.fixture
def mock_config():
    """建立 mock 配置"""
    config = MagicMock()
    config.risk_weights = {"BTC": 1.0, "ETH": 1.2}
    config.get_risk_weight = lambda s: config.risk_weights.get(s)
    config.monitor.volatility_lookback_days = 7
    return config


@pytest.fixture
def mock_client():
    """建立 mock API client"""
    return AsyncMock()


@pytest.fixture
def calculator(mock_config, mock_client):
    """建立 RiskCalculator"""
    return RiskCalculator(mock_config, mock_client)


def test_calculate_volatility():
    """測試波動率計算"""
    prices = [100, 102, 98, 105, 103, 101, 104]

    calc = RiskCalculator(MagicMock(), AsyncMock())
    volatility = calc._calculate_volatility(prices)

    assert volatility > 0
    assert isinstance(volatility, float)


def test_calculate_volatility_empty():
    """測試空價格列表"""
    calc = RiskCalculator(MagicMock(), AsyncMock())
    volatility = calc._calculate_volatility([])

    assert volatility == 1.0  # 預設值


def test_calculate_volatility_single():
    """測試單一價格"""
    calc = RiskCalculator(MagicMock(), AsyncMock())
    volatility = calc._calculate_volatility([100])

    assert volatility == 1.0  # 預設值


@pytest.mark.asyncio
async def test_get_risk_weight_from_config(calculator, mock_config):
    """測試從配置取得風險權重"""
    weight = await calculator.get_risk_weight("BTC")
    assert weight == 1.0

    weight = await calculator.get_risk_weight("ETH")
    assert weight == 1.2


@pytest.mark.asyncio
async def test_get_risk_weight_auto_calculate(calculator, mock_client):
    """測試自動計算風險權重"""
    # 設定 mock 回應
    mock_client.get_candles.return_value = [
        {"close": 100},
        {"close": 102},
        {"close": 98},
        {"close": 105},
        {"close": 103},
        {"close": 101},
        {"close": 104},
    ]

    weight = await calculator.get_risk_weight("DOGE")

    assert weight > 0
    mock_client.get_candles.assert_called()


@pytest.mark.asyncio
async def test_calculate_target_margins(calculator):
    """測試計算目標保證金"""
    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("0.5"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("500"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("2"),
        ),
        Position(
            symbol="ETH",
            side=PositionSide.LONG,
            quantity=Decimal("10"),
            entry_price=Decimal("3000"),
            current_price=Decimal("3000"),
            margin=Decimal("300"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1"),
        ),
    ]

    total_margin = Decimal("800")

    targets = await calculator.calculate_target_margins(positions, total_margin)

    assert "BTC" in targets
    assert "ETH" in targets
    # 總和應該等於 total_margin
    total = sum(targets.values())
    assert abs(total - total_margin) < Decimal("0.01")


@pytest.mark.asyncio
async def test_calculate_target_margins_with_risk_weights(calculator):
    """測試含風險權重的目標保證金計算"""
    # BTC 權重 1.0, ETH 權重 1.2
    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("500"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1"),
        ),
        Position(
            symbol="ETH",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("500"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1"),
        ),
    ]

    total_margin = Decimal("1000")

    targets = await calculator.calculate_target_margins(positions, total_margin)

    # ETH 應該分配更多（因為權重 1.2 > 1.0）
    assert targets["ETH"] > targets["BTC"]


@pytest.mark.asyncio
async def test_calculate_target_margins_empty_positions(calculator):
    """測試空倉位列表"""
    targets = await calculator.calculate_target_margins([], Decimal("1000"))
    assert targets == {}


def test_clear_cache(calculator):
    """測試清除快取"""
    calculator._volatility_cache["TEST"] = 1.5
    calculator.clear_cache()
    assert calculator._volatility_cache == {}


@pytest.mark.asyncio
async def test_get_risk_weight_uses_cache(calculator, mock_client):
    """測試風險權重快取機制"""
    # 預設 DOGE 的快取值
    calculator._volatility_cache["DOGE"] = 0.8

    weight = await calculator.get_risk_weight("DOGE")

    assert weight == 0.8
    # 應該不會呼叫 API
    mock_client.get_candles.assert_not_called()


@pytest.mark.asyncio
async def test_fetch_volatility_error_handling(calculator, mock_client):
    """測試 API 錯誤處理"""
    mock_client.get_candles.side_effect = Exception("API Error")

    volatility = await calculator._fetch_volatility("BTC")

    assert volatility == 1.0  # 錯誤時回傳預設值
