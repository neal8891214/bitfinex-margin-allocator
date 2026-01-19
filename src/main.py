"""Main Entry 模組：服務進入點與生命週期管理"""

import argparse
import asyncio
import logging
import signal
import sys
from pathlib import Path
from typing import Any, Callable, Coroutine, Optional

from src.api.bitfinex_client import BitfinexClient, BitfinexAPIError
from src.api.bitfinex_ws import BitfinexWebSocket
from src.config_manager import Config, load_config
from src.core.margin_allocator import MarginAllocator
from src.core.position_liquidator import PositionLiquidator
from src.core.risk_calculator import RiskCalculator
from src.notifier.telegram_bot import TelegramNotifier
from src.scheduler.event_detector import EventDetector
from src.scheduler.poll_scheduler import PollScheduler
from src.storage.database import Database

logger = logging.getLogger(__name__)


class ServiceComponents:
    """服務元件容器"""

    def __init__(self) -> None:
        self.db: Optional[Database] = None
        self.client: Optional[BitfinexClient] = None
        self.risk_calculator: Optional[RiskCalculator] = None
        self.allocator: Optional[MarginAllocator] = None
        self.liquidator: Optional[PositionLiquidator] = None
        self.notifier: Optional[TelegramNotifier] = None
        self.event_detector: Optional[EventDetector] = None
        self.poll_scheduler: Optional[PollScheduler] = None
        self.websocket: Optional[BitfinexWebSocket] = None


def setup_logging(config: Config) -> None:
    """設定 logging 根據配置檔

    Args:
        config: 配置物件
    """
    log_level = getattr(logging, config.logging.level.upper(), logging.INFO)
    log_file = Path(config.logging.file)

    # 確保日誌目錄存在
    log_file.parent.mkdir(parents=True, exist_ok=True)

    # 設定根 logger
    root_logger = logging.getLogger()
    root_logger.setLevel(log_level)

    # 清除既有 handler
    root_logger.handlers.clear()

    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(log_level)
    console_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    console_handler.setFormatter(console_format)
    root_logger.addHandler(console_handler)

    # File handler
    file_handler = logging.FileHandler(str(log_file), encoding="utf-8")
    file_handler.setLevel(log_level)
    file_format = logging.Formatter(
        "%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    file_handler.setFormatter(file_format)
    root_logger.addHandler(file_handler)

    logger.info(f"Logging initialized: level={config.logging.level}, file={log_file}")


def parse_args() -> argparse.Namespace:
    """解析命令列參數

    Returns:
        解析後的參數
    """
    parser = argparse.ArgumentParser(
        description="Bitfinex Margin Balancer - 逐倉模式下模擬全倉保證金行為",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument(
        "--config",
        type=str,
        default="config/config.yaml",
        help="配置檔路徑 (預設: config/config.yaml)",
    )

    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="乾跑模式：計算但不實際執行寫入操作",
    )

    return parser.parse_args()


def load_and_validate_config(config_path: str) -> Config:
    """載入並驗證配置

    Args:
        config_path: 配置檔路徑

    Returns:
        驗證後的配置物件

    Raises:
        FileNotFoundError: 配置檔不存在
        pydantic.ValidationError: 配置驗證失敗
    """
    logger.info(f"Loading configuration from {config_path}")
    config = load_config(config_path)
    logger.info("Configuration loaded successfully")
    return config


def initialize_components(config: Config, dry_run: bool = False) -> ServiceComponents:
    """初始化所有服務元件

    Args:
        config: 配置物件
        dry_run: 是否為乾跑模式

    Returns:
        包含所有元件的容器
    """
    components = ServiceComponents()

    # 1. Database
    components.db = Database(config.database.path)
    logger.info(f"Database initialized: {config.database.path}")

    # 2. BitfinexClient
    components.client = BitfinexClient(
        api_key=config.bitfinex.api_key,
        api_secret=config.bitfinex.api_secret,
        base_url=config.bitfinex.base_url,
    )
    logger.info("BitfinexClient initialized")

    # 3. RiskCalculator
    components.risk_calculator = RiskCalculator(
        config=config,
        client=components.client,
    )
    logger.info("RiskCalculator initialized")

    # 4. MarginAllocator
    components.allocator = MarginAllocator(
        config=config,
        risk_calculator=components.risk_calculator,
        client=components.client,
        db=components.db,
    )
    logger.info("MarginAllocator initialized")

    # 5. PositionLiquidator（考慮 dry_run 模式）
    # 如果命令列指定了 dry_run，覆蓋配置檔的設定
    if dry_run:
        # 建立一個新的 config 副本，強制啟用 dry_run
        from copy import deepcopy
        liquidation_config = deepcopy(config.liquidation)
        liquidation_config.dry_run = True
        # 直接修改 config（因為是 Pydantic model）
        config_for_liquidator = config.model_copy(
            update={"liquidation": liquidation_config}
        )
    else:
        config_for_liquidator = config

    components.liquidator = PositionLiquidator(
        config=config_for_liquidator,
        client=components.client,
        db=components.db,
    )
    logger.info(f"PositionLiquidator initialized (dry_run={dry_run or config.liquidation.dry_run})")

    # 6. TelegramNotifier
    components.notifier = TelegramNotifier(
        bot_token=config.telegram.bot_token,
        chat_id=config.telegram.chat_id,
        enabled=config.telegram.enabled,
    )
    logger.info(f"TelegramNotifier initialized (enabled={config.telegram.enabled})")

    # 7. EventDetector
    components.event_detector = EventDetector(
        config=config,
        allocator=components.allocator,
        notifier=components.notifier,
    )
    logger.info("EventDetector initialized")

    # 8. PollScheduler
    components.poll_scheduler = PollScheduler(
        config=config,
        client=components.client,
        risk_calculator=components.risk_calculator,
        allocator=components.allocator,
        liquidator=components.liquidator,
        notifier=components.notifier,
        db=components.db,
    )
    logger.info("PollScheduler initialized")

    # 9. BitfinexWebSocket
    components.websocket = BitfinexWebSocket(
        ws_url=config.bitfinex.ws_url,
        emergency_margin_rate=config.thresholds.emergency_margin_rate,
    )
    logger.info("BitfinexWebSocket initialized")

    return components


async def startup_checks(components: ServiceComponents) -> bool:
    """啟動前檢查

    Args:
        components: 服務元件容器

    Returns:
        檢查是否全部通過
    """
    logger.info("Running startup checks...")

    # 1. 初始化資料庫
    if components.db is not None:
        try:
            await components.db.initialize()
            logger.info("✓ Database connection OK")
        except Exception as e:
            logger.error(f"✗ Database initialization failed: {e}")
            return False

    # 2. 測試 API 連線
    if components.client is not None:
        try:
            # 嘗試取得帳戶資訊
            account_info = await components.client.get_account_info()
            logger.info(
                f"✓ Bitfinex API connection OK "
                f"(equity: {account_info['total_equity']:.2f} USDT, "
                f"positions: {account_info['position_count']})"
            )
        except BitfinexAPIError as e:
            logger.error(f"✗ Bitfinex API check failed: {e}")
            if components.notifier is not None:
                await components.notifier.send_api_error_alert(e, e.retry_count)
            return False
        except Exception as e:
            logger.error(f"✗ Bitfinex API check failed: {e}")
            return False

    # 3. 測試 WebSocket 連線
    if components.websocket is not None:
        try:
            connected = await components.websocket.connect()
            if connected:
                logger.info("✓ WebSocket connection OK")
            else:
                logger.warning("⚠ WebSocket connection failed, will retry during runtime")
        except Exception as e:
            logger.warning(f"⚠ WebSocket connection failed: {e}, will retry during runtime")

    logger.info("Startup checks completed")
    return True


async def run_websocket_monitor(
    components: ServiceComponents,
    config: Config,
) -> None:
    """運行 WebSocket 監控

    Args:
        components: 服務元件容器
        config: 配置物件
    """
    if components.websocket is None or components.event_detector is None:
        return

    if components.client is None:
        return

    # 定義價格更新回調
    async def on_price_update(symbol: str, price: Any) -> None:
        """處理價格更新"""
        if components.event_detector is None or components.client is None:
            return

        # 檢查價格急漲急跌
        is_spike = components.event_detector.on_price_update(symbol, price)

        if is_spike:
            logger.warning(f"Price spike detected for {symbol}")
            # 取得當前倉位並檢查緊急狀況
            try:
                positions = await components.client.get_positions()
                critical = components.event_detector.check_emergency_conditions(
                    positions
                )

                for pos in critical:
                    if pos.symbol == symbol:
                        available = await components.client.get_derivatives_balance()
                        await components.event_detector.handle_emergency(
                            pos, positions, available
                        )
                        break
            except Exception as e:
                logger.error(f"Error handling price spike: {e}")

    # 註冊回調
    components.websocket.on_message(on_price_update)

    # 初始訂閱：取得當前倉位並訂閱高風險倉位
    try:
        positions = await components.client.get_positions()
        await components.websocket.update_subscriptions(positions)
    except Exception as e:
        logger.error(f"Failed to initialize WebSocket subscriptions: {e}")

    # 開始監聽
    await components.websocket.start()

    # 定期更新訂閱列表
    while True:
        await asyncio.sleep(config.monitor.poll_interval_sec)
        try:
            positions = await components.client.get_positions()
            await components.websocket.update_subscriptions(positions)
        except Exception as e:
            logger.error(f"Failed to update WebSocket subscriptions: {e}")


async def shutdown(
    components: ServiceComponents,
    loop: asyncio.AbstractEventLoop,
) -> None:
    """優雅關閉服務

    Args:
        components: 服務元件容器
        loop: 事件迴圈
    """
    logger.info("Shutting down...")

    # 停止 PollScheduler
    if components.poll_scheduler is not None:
        await components.poll_scheduler.stop()
        logger.info("PollScheduler stopped")

    # 關閉 WebSocket
    if components.websocket is not None:
        await components.websocket.close()
        logger.info("WebSocket closed")

    # 關閉 BitfinexClient
    if components.client is not None:
        await components.client.close()
        logger.info("BitfinexClient closed")

    # 關閉 Database
    if components.db is not None:
        await components.db.close()
        logger.info("Database closed")

    logger.info("Shutdown complete")


async def main(config_path: str, dry_run: bool = False) -> int:
    """主進入點

    Args:
        config_path: 配置檔路徑
        dry_run: 是否為乾跑模式

    Returns:
        退出碼 (0=成功, 1=失敗)
    """
    components = ServiceComponents()
    shutdown_event = asyncio.Event()

    # 設定信號處理
    loop = asyncio.get_event_loop()

    def signal_handler(sig: signal.Signals) -> None:
        logger.info(f"Received signal {sig.name}")
        shutdown_event.set()

    # 在 Unix 系統上註冊信號處理
    for sig in (signal.SIGINT, signal.SIGTERM):
        # 使用 functools.partial 來避免 lambda 閉包問題
        loop.add_signal_handler(
            sig, lambda s=sig: signal_handler(s)  # type: ignore[misc]
        )

    try:
        # 1. 載入配置
        config = load_and_validate_config(config_path)

        # 2. 設定 logging
        setup_logging(config)

        if dry_run:
            logger.info("Running in DRY-RUN mode - no writes will be executed")

        # 3. 初始化元件
        components = initialize_components(config, dry_run)

        # 4. 啟動前檢查
        if not await startup_checks(components):
            logger.error("Startup checks failed, exiting")
            return 1

        # 5. 啟動服務
        logger.info("Starting services...")

        # 啟動 PollScheduler
        if components.poll_scheduler is not None:
            await components.poll_scheduler.start()

        # 建立 WebSocket 監控任務
        ws_task: Optional[asyncio.Task[None]] = None
        if components.websocket is not None and components.websocket.is_connected:
            ws_task = asyncio.create_task(
                run_websocket_monitor(components, config)
            )

        logger.info("Bitfinex Margin Balancer is running")

        # 發送啟動通知
        if components.notifier is not None:
            await components.notifier.send_message(
                "<b>✅ Bitfinex Margin Balancer 已啟動</b>\n"
                f"模式: {'DRY-RUN' if dry_run else '正常'}\n"
                f"輪詢間隔: {config.monitor.poll_interval_sec}s"
            )

        # 6. 等待關閉信號
        await shutdown_event.wait()

        # 7. 取消 WebSocket 任務
        if ws_task is not None:
            ws_task.cancel()
            try:
                await ws_task
            except asyncio.CancelledError:
                pass

        # 8. 關閉服務
        await shutdown(components, loop)

        # 發送關閉通知
        if components.notifier is not None and components.notifier.enabled:
            # 注意：此時 Bot 可能已無法發送訊息
            try:
                await components.notifier.send_message(
                    "<b>🛑 Bitfinex Margin Balancer 已停止</b>"
                )
            except Exception:
                pass

        return 0

    except FileNotFoundError as e:
        print(f"Error: Configuration file not found: {e}")
        return 1
    except Exception as e:
        logger.exception(f"Unexpected error: {e}")
        return 1
    finally:
        # 確保資源被釋放
        await shutdown(components, loop)


def run() -> None:
    """CLI 進入點"""
    args = parse_args()

    try:
        exit_code = asyncio.run(main(args.config, args.dry_run))
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\nInterrupted by user")
        sys.exit(130)


if __name__ == "__main__":
    run()
