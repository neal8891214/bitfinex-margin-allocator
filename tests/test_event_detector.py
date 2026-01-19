"""EventDetector 模組測試"""

from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio

from src.config_manager import Config, ThresholdsConfig
from src.core.margin_allocator import MarginAllocator, RebalanceResult
from src.notifier.telegram_bot import TelegramNotifier
from src.scheduler.event_detector import EventDetector
from src.storage.models import Position, PositionSide, MarginAdjustment


@pytest.fixture
def mock_config() -> Config:
    """建立測試用配置"""
    config = MagicMock(spec=Config)
    config.thresholds = ThresholdsConfig(
        min_adjustment_usdt=50.0,
        min_deviation_pct=5.0,
        emergency_margin_rate=2.0,  # 低於 2% 為緊急
        price_spike_pct=3.0,  # 價格變動超過 3% 為急漲急跌
        account_margin_rate_warning=3.0,  # 帳戶保證金率低於 3% 警告
    )
    return config


@pytest.fixture
def mock_allocator() -> MagicMock:
    """建立 mock allocator"""
    allocator = MagicMock(spec=MarginAllocator)
    allocator.emergency_rebalance = AsyncMock()
    return allocator


@pytest.fixture
def mock_notifier() -> MagicMock:
    """建立 mock notifier"""
    notifier = MagicMock(spec=TelegramNotifier)
    notifier.send_adjustment_report = AsyncMock(return_value=True)
    notifier.send_account_margin_warning = AsyncMock(return_value=True)
    return notifier


@pytest.fixture
def event_detector(
    mock_config: Config,
    mock_allocator: MagicMock,
    mock_notifier: MagicMock,
) -> EventDetector:
    """建立測試用 EventDetector"""
    return EventDetector(
        config=mock_config,
        allocator=mock_allocator,
        notifier=mock_notifier,
    )


@pytest.fixture
def sample_positions() -> list:
    """建立測試用倉位"""
    return [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("0.1"),
            entry_price=Decimal("50000"),
            current_price=Decimal("48000"),
            margin=Decimal("500"),
            leverage=10,
            unrealized_pnl=Decimal("-200"),
            margin_rate=Decimal("5.5"),  # 正常
        ),
        Position(
            symbol="ETH",
            side=PositionSide.LONG,
            quantity=Decimal("2"),
            entry_price=Decimal("3000"),
            current_price=Decimal("2900"),
            margin=Decimal("300"),
            leverage=20,
            unrealized_pnl=Decimal("-200"),
            margin_rate=Decimal("1.5"),  # 低於 2%，緊急
        ),
        Position(
            symbol="SOL",
            side=PositionSide.SHORT,
            quantity=Decimal("10"),
            entry_price=Decimal("100"),
            current_price=Decimal("105"),
            margin=Decimal("200"),
            leverage=5,
            unrealized_pnl=Decimal("-50"),
            margin_rate=Decimal("3.0"),  # 正常但接近警告
        ),
    ]


class TestCheckEmergencyConditions:
    """check_emergency_conditions 方法測試"""

    def test_no_critical_positions(
        self, event_detector: EventDetector
    ) -> None:
        """所有倉位保證金率正常時回傳空列表"""
        positions = [
            Position(
                symbol="BTC",
                side=PositionSide.LONG,
                quantity=Decimal("0.1"),
                entry_price=Decimal("50000"),
                current_price=Decimal("50000"),
                margin=Decimal("500"),
                leverage=10,
                unrealized_pnl=Decimal("0"),
                margin_rate=Decimal("5.0"),  # > 2%
            ),
        ]

        result = event_detector.check_emergency_conditions(positions)

        assert len(result) == 0

    def test_single_critical_position(
        self, event_detector: EventDetector
    ) -> None:
        """有一個倉位保證金率過低"""
        positions = [
            Position(
                symbol="BTC",
                side=PositionSide.LONG,
                quantity=Decimal("0.1"),
                entry_price=Decimal("50000"),
                current_price=Decimal("50000"),
                margin=Decimal("500"),
                leverage=10,
                unrealized_pnl=Decimal("0"),
                margin_rate=Decimal("5.0"),  # 正常
            ),
            Position(
                symbol="ETH",
                side=PositionSide.LONG,
                quantity=Decimal("2"),
                entry_price=Decimal("3000"),
                current_price=Decimal("2900"),
                margin=Decimal("100"),
                leverage=20,
                unrealized_pnl=Decimal("-200"),
                margin_rate=Decimal("1.5"),  # 緊急！
            ),
        ]

        result = event_detector.check_emergency_conditions(positions)

        assert len(result) == 1
        assert result[0].symbol == "ETH"

    def test_multiple_critical_positions(
        self, event_detector: EventDetector, sample_positions: list
    ) -> None:
        """多個倉位保證金率過低"""
        # 修改讓 BTC 也變成緊急
        sample_positions[0] = Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("0.1"),
            entry_price=Decimal("50000"),
            current_price=Decimal("48000"),
            margin=Decimal("100"),
            leverage=10,
            unrealized_pnl=Decimal("-200"),
            margin_rate=Decimal("1.8"),  # 緊急
        )

        result = event_detector.check_emergency_conditions(sample_positions)

        assert len(result) == 2
        symbols = [p.symbol for p in result]
        assert "BTC" in symbols
        assert "ETH" in symbols

    def test_exactly_at_threshold(
        self, event_detector: EventDetector
    ) -> None:
        """保證金率剛好等於閾值不算緊急"""
        positions = [
            Position(
                symbol="BTC",
                side=PositionSide.LONG,
                quantity=Decimal("0.1"),
                entry_price=Decimal("50000"),
                current_price=Decimal("50000"),
                margin=Decimal("500"),
                leverage=10,
                unrealized_pnl=Decimal("0"),
                margin_rate=Decimal("2.0"),  # 剛好等於閾值
            ),
        ]

        result = event_detector.check_emergency_conditions(positions)

        # 剛好等於閾值不算緊急（需小於）
        assert len(result) == 0


class TestOnPriceUpdate:
    """on_price_update 方法測試"""

    def test_no_spike_small_change(
        self, event_detector: EventDetector
    ) -> None:
        """小幅價格變動不觸發警報"""
        # 設定前一價格
        event_detector._price_cache["BTC"] = Decimal("50000")

        result = event_detector.on_price_update(
            symbol="BTC",
            price=Decimal("50500"),  # 1% 變動
        )

        assert result is False

    def test_spike_detected_increase(
        self, event_detector: EventDetector
    ) -> None:
        """價格急漲超過閾值"""
        # 設定前一價格
        event_detector._price_cache["BTC"] = Decimal("50000")

        result = event_detector.on_price_update(
            symbol="BTC",
            price=Decimal("52000"),  # 4% 上漲
        )

        assert result is True

    def test_spike_detected_decrease(
        self, event_detector: EventDetector
    ) -> None:
        """價格急跌超過閾值"""
        # 設定前一價格
        event_detector._price_cache["BTC"] = Decimal("50000")

        result = event_detector.on_price_update(
            symbol="BTC",
            price=Decimal("47000"),  # 6% 下跌
        )

        assert result is True

    def test_first_price_no_spike(
        self, event_detector: EventDetector
    ) -> None:
        """第一次收到價格（無前一價格）不觸發"""
        result = event_detector.on_price_update(
            symbol="BTC",
            price=Decimal("50000"),
        )

        assert result is False

    def test_price_cache_updated(
        self, event_detector: EventDetector
    ) -> None:
        """價格更新後快取被更新"""
        event_detector.on_price_update(
            symbol="BTC",
            price=Decimal("50000"),
        )

        assert event_detector._price_cache["BTC"] == Decimal("50000")

        event_detector.on_price_update(
            symbol="BTC",
            price=Decimal("51000"),
        )

        assert event_detector._price_cache["BTC"] == Decimal("51000")

    def test_with_explicit_prev_price(
        self, event_detector: EventDetector
    ) -> None:
        """使用明確提供的前一價格"""
        result = event_detector.on_price_update(
            symbol="BTC",
            price=Decimal("52000"),
            prev_price=Decimal("50000"),  # 4% 變動
        )

        assert result is True

    def test_exactly_at_threshold(
        self, event_detector: EventDetector
    ) -> None:
        """價格變動剛好等於閾值會觸發"""
        event_detector._price_cache["BTC"] = Decimal("100")

        result = event_detector.on_price_update(
            symbol="BTC",
            price=Decimal("103"),  # 剛好 3% 變動
        )

        assert result is True

    def test_zero_prev_price(
        self, event_detector: EventDetector
    ) -> None:
        """前一價格為零不觸發（避免除零錯誤）"""
        event_detector._price_cache["BTC"] = Decimal("0")

        result = event_detector.on_price_update(
            symbol="BTC",
            price=Decimal("50000"),
        )

        assert result is False


class TestCheckAccountMarginRate:
    """check_account_margin_rate 方法測試"""

    def test_margin_rate_healthy(
        self, event_detector: EventDetector
    ) -> None:
        """帳戶保證金率健康"""
        result = event_detector.check_account_margin_rate(
            total_equity=Decimal("10000"),
            total_margin=Decimal("2000"),  # 500% > 3%
        )

        assert result is False

    def test_margin_rate_warning(
        self, event_detector: EventDetector
    ) -> None:
        """帳戶保證金率過低觸發警告"""
        result = event_detector.check_account_margin_rate(
            total_equity=Decimal("100"),
            total_margin=Decimal("5000"),  # 2% < 3%
        )

        assert result is True

    def test_no_margin_no_warning(
        self, event_detector: EventDetector
    ) -> None:
        """沒有倉位（保證金為零）不觸發警告"""
        result = event_detector.check_account_margin_rate(
            total_equity=Decimal("10000"),
            total_margin=Decimal("0"),
        )

        assert result is False

    def test_exactly_at_threshold(
        self, event_detector: EventDetector
    ) -> None:
        """保證金率剛好等於閾值不觸發"""
        result = event_detector.check_account_margin_rate(
            total_equity=Decimal("300"),
            total_margin=Decimal("10000"),  # 3% 剛好等於閾值
        )

        # 剛好等於閾值不觸發（需小於）
        assert result is False

    def test_warning_state_reset_on_recovery(
        self, event_detector: EventDetector
    ) -> None:
        """恢復正常後警告狀態被重置"""
        event_detector._margin_warning_sent = True

        # 恢復正常
        event_detector.check_account_margin_rate(
            total_equity=Decimal("10000"),
            total_margin=Decimal("1000"),  # 1000% > 3%
        )

        assert event_detector._margin_warning_sent is False


class TestHandleEmergency:
    """handle_emergency 方法測試"""

    @pytest.mark.asyncio
    async def test_successful_emergency_rebalance(
        self,
        event_detector: EventDetector,
        mock_allocator: MagicMock,
        mock_notifier: MagicMock,
        sample_positions: list,
    ) -> None:
        """成功處理緊急狀態"""
        critical_position = sample_positions[1]  # ETH

        mock_allocator.emergency_rebalance.return_value = RebalanceResult(
            success_count=1,
            fail_count=0,
            total_adjusted=Decimal("100"),
            adjustments=[],
        )

        result = await event_detector.handle_emergency(
            critical_position=critical_position,
            positions=sample_positions,
            available_balance=Decimal("500"),
        )

        assert result is True
        mock_allocator.emergency_rebalance.assert_called_once_with(
            positions=sample_positions,
            critical_position=critical_position,
            available_balance=Decimal("500"),
        )
        mock_notifier.send_adjustment_report.assert_called_once()

    @pytest.mark.asyncio
    async def test_failed_emergency_rebalance(
        self,
        event_detector: EventDetector,
        mock_allocator: MagicMock,
        mock_notifier: MagicMock,
        sample_positions: list,
    ) -> None:
        """緊急重平衡失敗"""
        critical_position = sample_positions[1]

        mock_allocator.emergency_rebalance.return_value = RebalanceResult(
            success_count=0,
            fail_count=1,
            total_adjusted=Decimal("0"),
            adjustments=[],
        )

        result = await event_detector.handle_emergency(
            critical_position=critical_position,
            positions=sample_positions,
            available_balance=Decimal("500"),
        )

        assert result is False

    @pytest.mark.asyncio
    async def test_no_adjustment_needed(
        self,
        event_detector: EventDetector,
        mock_allocator: MagicMock,
        mock_notifier: MagicMock,
        sample_positions: list,
    ) -> None:
        """不需要調整的情況"""
        critical_position = sample_positions[1]

        mock_allocator.emergency_rebalance.return_value = RebalanceResult(
            success_count=0,
            fail_count=0,
            total_adjusted=Decimal("0"),
            adjustments=[],
        )

        result = await event_detector.handle_emergency(
            critical_position=critical_position,
            positions=sample_positions,
            available_balance=Decimal("500"),
        )

        assert result is True
        mock_notifier.send_adjustment_report.assert_not_called()


class TestHandleAccountMarginWarning:
    """handle_account_margin_warning 方法測試"""

    @pytest.mark.asyncio
    async def test_send_warning(
        self,
        event_detector: EventDetector,
        mock_notifier: MagicMock,
    ) -> None:
        """發送帳戶保證金率警告"""
        result = await event_detector.handle_account_margin_warning(
            margin_rate=2.5
        )

        assert result is True
        assert event_detector._margin_warning_sent is True
        mock_notifier.send_account_margin_warning.assert_called_once_with(2.5)

    @pytest.mark.asyncio
    async def test_no_duplicate_warning(
        self,
        event_detector: EventDetector,
        mock_notifier: MagicMock,
    ) -> None:
        """避免重複發送警告"""
        event_detector._margin_warning_sent = True

        result = await event_detector.handle_account_margin_warning(
            margin_rate=2.5
        )

        assert result is False
        mock_notifier.send_account_margin_warning.assert_not_called()


class TestHelperMethods:
    """輔助方法測試"""

    def test_get_cached_price_exists(
        self, event_detector: EventDetector
    ) -> None:
        """取得已快取的價格"""
        event_detector._price_cache["BTC"] = Decimal("50000")

        result = event_detector.get_cached_price("BTC")

        assert result == Decimal("50000")

    def test_get_cached_price_not_exists(
        self, event_detector: EventDetector
    ) -> None:
        """取得不存在的價格"""
        result = event_detector.get_cached_price("XYZ")

        assert result is None

    def test_clear_price_cache(
        self, event_detector: EventDetector
    ) -> None:
        """清除價格快取"""
        event_detector._price_cache["BTC"] = Decimal("50000")
        event_detector._price_cache["ETH"] = Decimal("3000")

        event_detector.clear_price_cache()

        assert len(event_detector._price_cache) == 0

    def test_reset_warning_state(
        self, event_detector: EventDetector
    ) -> None:
        """重置警告狀態"""
        event_detector._margin_warning_sent = True

        event_detector.reset_warning_state()

        assert event_detector._margin_warning_sent is False
