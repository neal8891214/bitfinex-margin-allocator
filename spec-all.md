# Bitfinex Margin Balancer Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** 建立一個獨立 Python 服務，在 Bitfinex 逐倉模式下模擬全倉保證金行為，支援 20+ 倉位的風險權重動態分配與自動減倉。

**Architecture:** 模組化獨立服務，使用 asyncio 處理並發。核心引擎負責風險計算與保證金分配，透過 REST API 執行調整，WebSocket 監控緊急事件，SQLite 記錄歷史，Telegram 推送通知。

**Tech Stack:** Python 3.11+, asyncio, aiohttp, websockets, SQLite, python-telegram-bot, PyYAML, pydantic

---

## Task 1: 專案初始化與基礎結構

**Files:**
- Create: `bitfinex-margin-balancer/pyproject.toml`
- Create: `bitfinex-margin-balancer/requirements.txt`
- Create: `bitfinex-margin-balancer/config/config.example.yaml`
- Create: `bitfinex-margin-balancer/src/__init__.py`

**Step 1: 建立專案目錄結構**

```bash
mkdir -p bitfinex-margin-balancer/{config,src/{api,core,scheduler,notifier,storage},tests,logs}
touch bitfinex-margin-balancer/src/__init__.py
touch bitfinex-margin-balancer/src/api/__init__.py
touch bitfinex-margin-balancer/src/core/__init__.py
touch bitfinex-margin-balancer/src/scheduler/__init__.py
touch bitfinex-margin-balancer/src/notifier/__init__.py
touch bitfinex-margin-balancer/src/storage/__init__.py
```

**Step 2: 建立 pyproject.toml**

```toml
[project]
name = "bitfinex-margin-balancer"
version = "0.1.0"
description = "Simulate cross-margin behavior on Bitfinex isolated margin"
requires-python = ">=3.11"
dependencies = [
    "aiohttp>=3.9.0",
    "websockets>=12.0",
    "pydantic>=2.5.0",
    "pydantic-settings>=2.1.0",
    "PyYAML>=6.0",
    "python-telegram-bot>=20.7",
    "aiosqlite>=0.19.0",
    "numpy>=1.26.0",
]

[project.optional-dependencies]
dev = [
    "pytest>=7.4.0",
    "pytest-asyncio>=0.21.0",
    "pytest-cov>=4.1.0",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"
```

**Step 3: 建立 requirements.txt**

```text
aiohttp>=3.9.0
websockets>=12.0
pydantic>=2.5.0
pydantic-settings>=2.1.0
PyYAML>=6.0
python-telegram-bot>=20.7
aiosqlite>=0.19.0
numpy>=1.26.0
pytest>=7.4.0
pytest-asyncio>=0.21.0
pytest-cov>=4.1.0
```

**Step 4: 建立範例配置檔**

```yaml
# config/config.example.yaml

# Bitfinex API (從環境變數讀取)
bitfinex:
  api_key: ${BITFINEX_API_KEY}
  api_secret: ${BITFINEX_API_SECRET}
  base_url: "https://api.bitfinex.com"
  ws_url: "wss://api.bitfinex.com/ws/2"

# Telegram 通知
telegram:
  bot_token: ${TELEGRAM_BOT_TOKEN}
  chat_id: ${TELEGRAM_CHAT_ID}
  enabled: true

# 監控設定
monitor:
  poll_interval_sec: 60
  volatility_update_hours: 1
  volatility_lookback_days: 7
  heartbeat_interval_sec: 300

# 觸發閾值
thresholds:
  min_adjustment_usdt: 50
  min_deviation_pct: 5
  emergency_margin_rate: 2.0
  price_spike_pct: 3.0
  account_margin_rate_warning: 3.0

# 風險權重（手動覆蓋，未列出的幣種使用自動計算）
risk_weights:
  BTC: 1.0
  ETH: 1.2

# 倉位優先級（減倉用，數字越大越不會被平）
position_priority:
  BTC: 100
  ETH: 90
  default: 50

# 減倉設定
liquidation:
  enabled: true
  require_confirmation: false
  max_single_close_pct: 25
  cooldown_seconds: 30
  safety_margin_multiplier: 3.0
  dry_run: true

# 資料庫
database:
  path: "data/margin_balancer.db"

# 日誌
logging:
  level: "INFO"
  file: "logs/margin_balancer.log"
```

**Step 5: Commit**

```bash
cd bitfinex-margin-balancer
git init
git add .
git commit -m "chore: initialize project structure"
```

---

## Task 2: Config Manager 模組

**Files:**
- Create: `src/config_manager.py`
- Create: `tests/test_config_manager.py`

**Step 1: 寫 Config Manager 的 failing test**

```python
# tests/test_config_manager.py
import pytest
from pathlib import Path
from src.config_manager import Config, load_config


def test_load_config_from_yaml(tmp_path):
    """測試從 YAML 檔載入配置"""
    config_content = """
bitfinex:
  api_key: test_key
  api_secret: test_secret
  base_url: https://api.bitfinex.com
  ws_url: wss://api.bitfinex.com/ws/2

telegram:
  bot_token: test_token
  chat_id: "123456"
  enabled: false

monitor:
  poll_interval_sec: 30
  volatility_update_hours: 2
  volatility_lookback_days: 14
  heartbeat_interval_sec: 600

thresholds:
  min_adjustment_usdt: 100
  min_deviation_pct: 10
  emergency_margin_rate: 1.5
  price_spike_pct: 5.0
  account_margin_rate_warning: 2.5

risk_weights:
  BTC: 1.0
  ETH: 1.5

position_priority:
  BTC: 100
  default: 50

liquidation:
  enabled: true
  require_confirmation: true
  max_single_close_pct: 20
  cooldown_seconds: 60
  safety_margin_multiplier: 2.0
  dry_run: true

database:
  path: data/test.db

logging:
  level: DEBUG
  file: logs/test.log
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(config_content)

    config = load_config(config_file)

    assert config.bitfinex.api_key == "test_key"
    assert config.monitor.poll_interval_sec == 30
    assert config.thresholds.min_adjustment_usdt == 100
    assert config.risk_weights["BTC"] == 1.0
    assert config.position_priority["ETH"] == 50  # 使用 default
    assert config.liquidation.dry_run is True


def test_config_env_variable_substitution(tmp_path, monkeypatch):
    """測試環境變數替換"""
    monkeypatch.setenv("BITFINEX_API_KEY", "env_key_123")
    monkeypatch.setenv("BITFINEX_API_SECRET", "env_secret_456")

    config_content = """
bitfinex:
  api_key: ${BITFINEX_API_KEY}
  api_secret: ${BITFINEX_API_SECRET}
  base_url: https://api.bitfinex.com
  ws_url: wss://api.bitfinex.com/ws/2

telegram:
  bot_token: token
  chat_id: "123"
  enabled: false

monitor:
  poll_interval_sec: 60
  volatility_update_hours: 1
  volatility_lookback_days: 7
  heartbeat_interval_sec: 300

thresholds:
  min_adjustment_usdt: 50
  min_deviation_pct: 5
  emergency_margin_rate: 2.0
  price_spike_pct: 3.0
  account_margin_rate_warning: 3.0

risk_weights: {}
position_priority:
  default: 50

liquidation:
  enabled: false
  require_confirmation: false
  max_single_close_pct: 25
  cooldown_seconds: 30
  safety_margin_multiplier: 3.0
  dry_run: true

database:
  path: data/test.db

logging:
  level: INFO
  file: logs/test.log
"""
    config_file = tmp_path / "config.yaml"
    config_file.write_text(config_content)

    config = load_config(config_file)

    assert config.bitfinex.api_key == "env_key_123"
    assert config.bitfinex.api_secret == "env_secret_456"


def test_get_risk_weight_with_default():
    """測試取得風險權重，含預設值"""
    from src.config_manager import Config

    config = Config(
        bitfinex={"api_key": "k", "api_secret": "s", "base_url": "u", "ws_url": "w"},
        telegram={"bot_token": "t", "chat_id": "c", "enabled": False},
        monitor={"poll_interval_sec": 60, "volatility_update_hours": 1, "volatility_lookback_days": 7, "heartbeat_interval_sec": 300},
        thresholds={"min_adjustment_usdt": 50, "min_deviation_pct": 5, "emergency_margin_rate": 2, "price_spike_pct": 3, "account_margin_rate_warning": 3},
        risk_weights={"BTC": 1.0, "ETH": 1.2},
        position_priority={"BTC": 100, "default": 50},
        liquidation={"enabled": False, "require_confirmation": False, "max_single_close_pct": 25, "cooldown_seconds": 30, "safety_margin_multiplier": 3, "dry_run": True},
        database={"path": "data/test.db"},
        logging={"level": "INFO", "file": "logs/test.log"},
    )

    assert config.get_risk_weight("BTC") == 1.0
    assert config.get_risk_weight("ETH") == 1.2
    assert config.get_risk_weight("DOGE") is None  # 未配置，回傳 None（由 RiskCalculator 自動計算）


def test_get_position_priority_with_default():
    """測試取得倉位優先級，含預設值"""
    from src.config_manager import Config

    config = Config(
        bitfinex={"api_key": "k", "api_secret": "s", "base_url": "u", "ws_url": "w"},
        telegram={"bot_token": "t", "chat_id": "c", "enabled": False},
        monitor={"poll_interval_sec": 60, "volatility_update_hours": 1, "volatility_lookback_days": 7, "heartbeat_interval_sec": 300},
        thresholds={"min_adjustment_usdt": 50, "min_deviation_pct": 5, "emergency_margin_rate": 2, "price_spike_pct": 3, "account_margin_rate_warning": 3},
        risk_weights={},
        position_priority={"BTC": 100, "ETH": 90, "default": 50},
        liquidation={"enabled": False, "require_confirmation": False, "max_single_close_pct": 25, "cooldown_seconds": 30, "safety_margin_multiplier": 3, "dry_run": True},
        database={"path": "data/test.db"},
        logging={"level": "INFO", "file": "logs/test.log"},
    )

    assert config.get_position_priority("BTC") == 100
    assert config.get_position_priority("ETH") == 90
    assert config.get_position_priority("DOGE") == 50  # 使用 default
```

**Step 2: 執行測試確認失敗**

```bash
cd bitfinex-margin-balancer
pytest tests/test_config_manager.py -v
```

Expected: FAIL with "ModuleNotFoundError: No module named 'src.config_manager'"

**Step 3: 實作 Config Manager**

```python
# src/config_manager.py
"""配置管理模組：載入 YAML 配置並支援環境變數替換"""

import os
import re
from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel


class BitfinexConfig(BaseModel):
    api_key: str
    api_secret: str
    base_url: str
    ws_url: str


class TelegramConfig(BaseModel):
    bot_token: str
    chat_id: str
    enabled: bool = True


class MonitorConfig(BaseModel):
    poll_interval_sec: int = 60
    volatility_update_hours: int = 1
    volatility_lookback_days: int = 7
    heartbeat_interval_sec: int = 300


class ThresholdsConfig(BaseModel):
    min_adjustment_usdt: float = 50
    min_deviation_pct: float = 5
    emergency_margin_rate: float = 2.0
    price_spike_pct: float = 3.0
    account_margin_rate_warning: float = 3.0


class LiquidationConfig(BaseModel):
    enabled: bool = True
    require_confirmation: bool = False
    max_single_close_pct: float = 25
    cooldown_seconds: int = 30
    safety_margin_multiplier: float = 3.0
    dry_run: bool = True


class DatabaseConfig(BaseModel):
    path: str = "data/margin_balancer.db"


class LoggingConfig(BaseModel):
    level: str = "INFO"
    file: str = "logs/margin_balancer.log"


class Config(BaseModel):
    bitfinex: BitfinexConfig
    telegram: TelegramConfig
    monitor: MonitorConfig
    thresholds: ThresholdsConfig
    risk_weights: dict[str, float] = {}
    position_priority: dict[str, int]
    liquidation: LiquidationConfig
    database: DatabaseConfig
    logging: LoggingConfig

    def get_risk_weight(self, symbol: str) -> float | None:
        """取得幣種的風險權重，未配置則回傳 None"""
        return self.risk_weights.get(symbol)

    def get_position_priority(self, symbol: str) -> int:
        """取得倉位優先級，未配置則使用 default"""
        return self.position_priority.get(symbol, self.position_priority.get("default", 50))


def _substitute_env_vars(value: Any) -> Any:
    """遞迴替換字串中的 ${ENV_VAR} 為環境變數值"""
    if isinstance(value, str):
        pattern = r"\$\{([^}]+)\}"
        matches = re.findall(pattern, value)
        for match in matches:
            env_value = os.environ.get(match, "")
            value = value.replace(f"${{{match}}}", env_value)
        return value
    elif isinstance(value, dict):
        return {k: _substitute_env_vars(v) for k, v in value.items()}
    elif isinstance(value, list):
        return [_substitute_env_vars(item) for item in value]
    return value


def load_config(config_path: Path | str) -> Config:
    """從 YAML 檔載入配置"""
    config_path = Path(config_path)

    if not config_path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(config_path, "r", encoding="utf-8") as f:
        raw_config = yaml.safe_load(f)

    # 替換環境變數
    config_data = _substitute_env_vars(raw_config)

    return Config(**config_data)
```

**Step 4: 執行測試確認通過**

```bash
pytest tests/test_config_manager.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/config_manager.py tests/test_config_manager.py
git commit -m "feat: add config manager with env var substitution"
```

---

## Task 3: Data Models 模組

**Files:**
- Create: `src/storage/models.py`
- Create: `tests/test_models.py`

**Step 1: 寫 Models 的 failing test**

```python
# tests/test_models.py
import pytest
from datetime import datetime
from decimal import Decimal
from src.storage.models import (
    Position,
    MarginAdjustment,
    Liquidation,
    AccountSnapshot,
    AdjustmentDirection,
    TriggerType,
    PositionSide,
)


def test_position_model():
    """測試 Position 資料模型"""
    pos = Position(
        symbol="BTC",
        side=PositionSide.LONG,
        quantity=Decimal("0.5"),
        entry_price=Decimal("50000"),
        current_price=Decimal("51000"),
        margin=Decimal("500"),
        leverage=10,
        unrealized_pnl=Decimal("500"),
        margin_rate=Decimal("5.0"),
    )

    assert pos.symbol == "BTC"
    assert pos.side == PositionSide.LONG
    assert pos.notional_value == Decimal("25500")  # 0.5 * 51000
    assert pos.is_profitable is True


def test_position_notional_value_short():
    """測試 Short 倉位的名義價值"""
    pos = Position(
        symbol="ETH",
        side=PositionSide.SHORT,
        quantity=Decimal("10"),
        entry_price=Decimal("3000"),
        current_price=Decimal("2900"),
        margin=Decimal("300"),
        leverage=10,
        unrealized_pnl=Decimal("1000"),
        margin_rate=Decimal("10.0"),
    )

    assert pos.notional_value == Decimal("29000")  # 10 * 2900
    assert pos.is_profitable is True


def test_margin_adjustment_model():
    """測試 MarginAdjustment 資料模型"""
    adj = MarginAdjustment(
        timestamp=datetime(2026, 1, 19, 12, 0, 0),
        symbol="BTC",
        direction=AdjustmentDirection.INCREASE,
        amount=Decimal("100"),
        before_margin=Decimal("400"),
        after_margin=Decimal("500"),
        trigger_type=TriggerType.SCHEDULED,
    )

    assert adj.symbol == "BTC"
    assert adj.direction == AdjustmentDirection.INCREASE
    assert adj.amount == Decimal("100")


def test_liquidation_model():
    """測試 Liquidation 資料模型"""
    liq = Liquidation(
        timestamp=datetime(2026, 1, 19, 12, 0, 0),
        symbol="DOGE",
        side=PositionSide.LONG,
        quantity=Decimal("1000"),
        price=Decimal("0.1"),
        released_margin=Decimal("50"),
        reason="Insufficient margin",
    )

    assert liq.symbol == "DOGE"
    assert liq.released_margin == Decimal("50")


def test_account_snapshot_model():
    """測試 AccountSnapshot 資料模型"""
    positions = [
        {"symbol": "BTC", "margin": 500},
        {"symbol": "ETH", "margin": 300},
    ]
    snap = AccountSnapshot(
        timestamp=datetime(2026, 1, 19, 12, 0, 0),
        total_equity=Decimal("10000"),
        total_margin=Decimal("800"),
        available_balance=Decimal("9200"),
        positions_json=positions,
    )

    assert snap.total_equity == Decimal("10000")
    assert len(snap.positions_json) == 2
```

**Step 2: 執行測試確認失敗**

```bash
pytest tests/test_models.py -v
```

Expected: FAIL with "ModuleNotFoundError"

**Step 3: 實作 Models**

```python
# src/storage/models.py
"""資料模型定義"""

from datetime import datetime
from decimal import Decimal
from enum import Enum
from typing import Any

from pydantic import BaseModel, computed_field


class PositionSide(str, Enum):
    LONG = "long"
    SHORT = "short"


class AdjustmentDirection(str, Enum):
    INCREASE = "increase"
    DECREASE = "decrease"


class TriggerType(str, Enum):
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

    @computed_field
    @property
    def notional_value(self) -> Decimal:
        """名義價值 = 數量 × 當前價格"""
        return self.quantity * self.current_price

    @computed_field
    @property
    def is_profitable(self) -> bool:
        """是否獲利"""
        return self.unrealized_pnl > 0


class MarginAdjustment(BaseModel):
    """保證金調整記錄"""

    id: int | None = None
    timestamp: datetime
    symbol: str
    direction: AdjustmentDirection
    amount: Decimal
    before_margin: Decimal
    after_margin: Decimal
    trigger_type: TriggerType


class Liquidation(BaseModel):
    """減倉記錄"""

    id: int | None = None
    timestamp: datetime
    symbol: str
    side: PositionSide
    quantity: Decimal
    price: Decimal
    released_margin: Decimal
    reason: str


class AccountSnapshot(BaseModel):
    """帳戶快照"""

    id: int | None = None
    timestamp: datetime
    total_equity: Decimal
    total_margin: Decimal
    available_balance: Decimal
    positions_json: list[dict[str, Any]]
```

**Step 4: 執行測試確認通過**

```bash
pytest tests/test_models.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/storage/models.py tests/test_models.py
git commit -m "feat: add data models for positions, adjustments, liquidations"
```

---

## Task 4: Database 模組

**Files:**
- Create: `src/storage/database.py`
- Create: `tests/test_database.py`

**Step 1: 寫 Database 的 failing test**

```python
# tests/test_database.py
import pytest
import pytest_asyncio
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from src.storage.database import Database
from src.storage.models import (
    MarginAdjustment,
    Liquidation,
    AccountSnapshot,
    AdjustmentDirection,
    TriggerType,
    PositionSide,
)


@pytest_asyncio.fixture
async def db(tmp_path):
    """建立測試用資料庫"""
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_database_initialize(db):
    """測試資料庫初始化"""
    # 檢查表是否存在
    tables = await db.get_tables()
    assert "margin_adjustments" in tables
    assert "liquidations" in tables
    assert "account_snapshots" in tables


@pytest.mark.asyncio
async def test_save_and_get_margin_adjustment(db):
    """測試儲存和讀取保證金調整記錄"""
    adj = MarginAdjustment(
        timestamp=datetime(2026, 1, 19, 12, 0, 0),
        symbol="BTC",
        direction=AdjustmentDirection.INCREASE,
        amount=Decimal("100"),
        before_margin=Decimal("400"),
        after_margin=Decimal("500"),
        trigger_type=TriggerType.SCHEDULED,
    )

    saved_id = await db.save_margin_adjustment(adj)
    assert saved_id is not None

    records = await db.get_margin_adjustments(limit=10)
    assert len(records) == 1
    assert records[0].symbol == "BTC"
    assert records[0].amount == Decimal("100")


@pytest.mark.asyncio
async def test_save_and_get_liquidation(db):
    """測試儲存和讀取減倉記錄"""
    liq = Liquidation(
        timestamp=datetime(2026, 1, 19, 12, 0, 0),
        symbol="DOGE",
        side=PositionSide.LONG,
        quantity=Decimal("1000"),
        price=Decimal("0.1"),
        released_margin=Decimal("50"),
        reason="Insufficient margin",
    )

    saved_id = await db.save_liquidation(liq)
    assert saved_id is not None

    records = await db.get_liquidations(limit=10)
    assert len(records) == 1
    assert records[0].symbol == "DOGE"


@pytest.mark.asyncio
async def test_save_and_get_account_snapshot(db):
    """測試儲存和讀取帳戶快照"""
    snap = AccountSnapshot(
        timestamp=datetime(2026, 1, 19, 12, 0, 0),
        total_equity=Decimal("10000"),
        total_margin=Decimal("800"),
        available_balance=Decimal("9200"),
        positions_json=[{"symbol": "BTC", "margin": 500}],
    )

    saved_id = await db.save_account_snapshot(snap)
    assert saved_id is not None

    records = await db.get_account_snapshots(limit=10)
    assert len(records) == 1
    assert records[0].total_equity == Decimal("10000")


@pytest.mark.asyncio
async def test_get_daily_stats(db):
    """測試取得每日統計"""
    # 新增多筆調整記錄
    for i in range(5):
        adj = MarginAdjustment(
            timestamp=datetime(2026, 1, 19, 12, i, 0),
            symbol="BTC",
            direction=AdjustmentDirection.INCREASE,
            amount=Decimal("100"),
            before_margin=Decimal("400"),
            after_margin=Decimal("500"),
            trigger_type=TriggerType.SCHEDULED,
        )
        await db.save_margin_adjustment(adj)

    # 新增一筆減倉記錄
    liq = Liquidation(
        timestamp=datetime(2026, 1, 19, 12, 0, 0),
        symbol="DOGE",
        side=PositionSide.LONG,
        quantity=Decimal("1000"),
        price=Decimal("0.1"),
        released_margin=Decimal("50"),
        reason="Test",
    )
    await db.save_liquidation(liq)

    stats = await db.get_daily_stats(datetime(2026, 1, 19).date())
    assert stats["adjustment_count"] == 5
    assert stats["liquidation_count"] == 1
```

**Step 2: 執行測試確認失敗**

```bash
pytest tests/test_database.py -v
```

Expected: FAIL

**Step 3: 實作 Database**

```python
# src/storage/database.py
"""SQLite 資料庫操作模組"""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import aiosqlite

from .models import (
    MarginAdjustment,
    Liquidation,
    AccountSnapshot,
    AdjustmentDirection,
    TriggerType,
    PositionSide,
)


class Database:
    """非同步 SQLite 資料庫操作"""

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn: aiosqlite.Connection | None = None

    async def initialize(self) -> None:
        """初始化資料庫連線並建立表"""
        self._conn = await aiosqlite.connect(str(self.db_path))
        self._conn.row_factory = aiosqlite.Row
        await self._create_tables()

    async def close(self) -> None:
        """關閉資料庫連線"""
        if self._conn:
            await self._conn.close()
            self._conn = None

    async def _create_tables(self) -> None:
        """建立資料表"""
        await self._conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS margin_adjustments (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                symbol TEXT NOT NULL,
                direction TEXT NOT NULL,
                amount DECIMAL NOT NULL,
                before_margin DECIMAL NOT NULL,
                after_margin DECIMAL NOT NULL,
                trigger_type TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS liquidations (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                symbol TEXT NOT NULL,
                side TEXT NOT NULL,
                quantity DECIMAL NOT NULL,
                price DECIMAL NOT NULL,
                released_margin DECIMAL NOT NULL,
                reason TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS account_snapshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp DATETIME NOT NULL,
                total_equity DECIMAL NOT NULL,
                total_margin DECIMAL NOT NULL,
                available_balance DECIMAL NOT NULL,
                positions_json TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_adjustments_timestamp ON margin_adjustments(timestamp);
            CREATE INDEX IF NOT EXISTS idx_liquidations_timestamp ON liquidations(timestamp);
            CREATE INDEX IF NOT EXISTS idx_snapshots_timestamp ON account_snapshots(timestamp);
            """
        )
        await self._conn.commit()

    async def get_tables(self) -> list[str]:
        """取得所有表名"""
        cursor = await self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        rows = await cursor.fetchall()
        return [row["name"] for row in rows]

    async def save_margin_adjustment(self, adj: MarginAdjustment) -> int:
        """儲存保證金調整記錄"""
        cursor = await self._conn.execute(
            """
            INSERT INTO margin_adjustments
            (timestamp, symbol, direction, amount, before_margin, after_margin, trigger_type)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                adj.timestamp.isoformat(),
                adj.symbol,
                adj.direction.value,
                str(adj.amount),
                str(adj.before_margin),
                str(adj.after_margin),
                adj.trigger_type.value,
            ),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_margin_adjustments(
        self, limit: int = 100, symbol: str | None = None
    ) -> list[MarginAdjustment]:
        """取得保證金調整記錄"""
        query = "SELECT * FROM margin_adjustments"
        params: list[Any] = []

        if symbol:
            query += " WHERE symbol = ?"
            params.append(symbol)

        query += " ORDER BY timestamp DESC LIMIT ?"
        params.append(limit)

        cursor = await self._conn.execute(query, params)
        rows = await cursor.fetchall()

        return [
            MarginAdjustment(
                id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                symbol=row["symbol"],
                direction=AdjustmentDirection(row["direction"]),
                amount=Decimal(row["amount"]),
                before_margin=Decimal(row["before_margin"]),
                after_margin=Decimal(row["after_margin"]),
                trigger_type=TriggerType(row["trigger_type"]),
            )
            for row in rows
        ]

    async def save_liquidation(self, liq: Liquidation) -> int:
        """儲存減倉記錄"""
        cursor = await self._conn.execute(
            """
            INSERT INTO liquidations
            (timestamp, symbol, side, quantity, price, released_margin, reason)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                liq.timestamp.isoformat(),
                liq.symbol,
                liq.side.value,
                str(liq.quantity),
                str(liq.price),
                str(liq.released_margin),
                liq.reason,
            ),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_liquidations(self, limit: int = 100) -> list[Liquidation]:
        """取得減倉記錄"""
        cursor = await self._conn.execute(
            "SELECT * FROM liquidations ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()

        return [
            Liquidation(
                id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                symbol=row["symbol"],
                side=PositionSide(row["side"]),
                quantity=Decimal(row["quantity"]),
                price=Decimal(row["price"]),
                released_margin=Decimal(row["released_margin"]),
                reason=row["reason"],
            )
            for row in rows
        ]

    async def save_account_snapshot(self, snap: AccountSnapshot) -> int:
        """儲存帳戶快照"""
        cursor = await self._conn.execute(
            """
            INSERT INTO account_snapshots
            (timestamp, total_equity, total_margin, available_balance, positions_json)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                snap.timestamp.isoformat(),
                str(snap.total_equity),
                str(snap.total_margin),
                str(snap.available_balance),
                json.dumps(snap.positions_json),
            ),
        )
        await self._conn.commit()
        return cursor.lastrowid

    async def get_account_snapshots(self, limit: int = 100) -> list[AccountSnapshot]:
        """取得帳戶快照"""
        cursor = await self._conn.execute(
            "SELECT * FROM account_snapshots ORDER BY timestamp DESC LIMIT ?",
            (limit,),
        )
        rows = await cursor.fetchall()

        return [
            AccountSnapshot(
                id=row["id"],
                timestamp=datetime.fromisoformat(row["timestamp"]),
                total_equity=Decimal(row["total_equity"]),
                total_margin=Decimal(row["total_margin"]),
                available_balance=Decimal(row["available_balance"]),
                positions_json=json.loads(row["positions_json"]),
            )
            for row in rows
        ]

    async def get_daily_stats(self, target_date: date) -> dict[str, int]:
        """取得指定日期的統計"""
        date_str = target_date.isoformat()

        cursor = await self._conn.execute(
            """
            SELECT COUNT(*) as count FROM margin_adjustments
            WHERE date(timestamp) = ?
            """,
            (date_str,),
        )
        adj_row = await cursor.fetchone()

        cursor = await self._conn.execute(
            """
            SELECT COUNT(*) as count FROM liquidations
            WHERE date(timestamp) = ?
            """,
            (date_str,),
        )
        liq_row = await cursor.fetchone()

        return {
            "adjustment_count": adj_row["count"],
            "liquidation_count": liq_row["count"],
        }
```

**Step 4: 執行測試確認通過**

```bash
pytest tests/test_database.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/storage/database.py tests/test_database.py
git commit -m "feat: add async SQLite database module"
```

---

## Task 5: Bitfinex API Client 模組

**Files:**
- Create: `src/api/bitfinex_client.py`
- Create: `tests/test_bitfinex_client.py`

**Step 1: 寫 Bitfinex Client 的 failing test**

```python
# tests/test_bitfinex_client.py
import pytest
import pytest_asyncio
from decimal import Decimal
from unittest.mock import AsyncMock, patch, MagicMock

from src.api.bitfinex_client import BitfinexClient
from src.storage.models import Position, PositionSide


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
    assert len(signature) == 128  # SHA384 hex length


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
    assert candles[0]["close"] == 51200


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
```

**Step 2: 執行測試確認失敗**

```bash
pytest tests/test_bitfinex_client.py -v
```

Expected: FAIL

**Step 3: 實作 Bitfinex Client**

```python
# src/api/bitfinex_client.py
"""Bitfinex REST API 客戶端"""

import hashlib
import hmac
import json
import time
from decimal import Decimal
from typing import Any

import aiohttp

from src.storage.models import Position, PositionSide


class BitfinexClient:
    """Bitfinex API 客戶端"""

    def __init__(self, api_key: str, api_secret: str, base_url: str):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self._session: aiohttp.ClientSession | None = None

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
        self, method: str, path: str, body: dict | None = None
    ) -> Any:
        """發送已認證請求"""
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

    def _parse_position(self, raw: list) -> Position:
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

    async def get_positions(self) -> list[Position]:
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

    async def get_account_info(self) -> dict:
        """取得帳戶資訊"""
        wallets = await self._request("POST", "/v2/auth/r/wallets")
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

    async def update_position_margin(
        self, symbol: str, delta: Decimal
    ) -> bool:
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

    async def get_candles(
        self, symbol: str, timeframe: str = "1D", limit: int = 7
    ) -> list[dict]:
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

    def get_full_symbol(self, symbol: str) -> str:
        """將簡短符號轉換為完整衍生品符號

        Args:
            symbol: 簡短符號，如 "BTC"

        Returns:
            完整符號，如 "tBTCF0:USTF0"
        """
        return f"t{symbol}F0:USTF0"
```

**Step 4: 執行測試確認通過**

```bash
pytest tests/test_bitfinex_client.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/api/bitfinex_client.py tests/test_bitfinex_client.py
git commit -m "feat: add Bitfinex REST API client"
```

---

## Task 6: Risk Calculator 模組

**Files:**
- Create: `src/core/risk_calculator.py`
- Create: `tests/test_risk_calculator.py`

**Step 1: 寫 Risk Calculator 的 failing test**

```python
# tests/test_risk_calculator.py
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
    mock_client.get_candles.assert_called_once()


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
```

**Step 2: 執行測試確認失敗**

```bash
pytest tests/test_risk_calculator.py -v
```

Expected: FAIL

**Step 3: 實作 Risk Calculator**

```python
# src/core/risk_calculator.py
"""風險計算模組：計算波動率與風險權重"""

from decimal import Decimal
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from src.api.bitfinex_client import BitfinexClient
    from src.config_manager import Config

from src.storage.models import Position


class RiskCalculator:
    """風險計算器"""

    def __init__(self, config: "Config", client: "BitfinexClient"):
        self.config = config
        self.client = client
        self._volatility_cache: dict[str, float] = {}

    def _calculate_volatility(self, prices: list[float]) -> float:
        """計算價格序列的波動率（標準差）

        Args:
            prices: 收盤價列表

        Returns:
            波動率（相對於平均價格的百分比）
        """
        if len(prices) < 2:
            return 1.0  # 預設值

        returns = np.diff(prices) / prices[:-1]
        volatility = float(np.std(returns))

        # 轉換為年化波動率的簡化版本
        # 返回相對值，用於比較不同幣種
        return max(volatility, 0.001)  # 確保不為零

    async def _fetch_volatility(self, symbol: str) -> float:
        """從 API 取得歷史價格並計算波動率"""
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
        positions: list[Position],
        total_available_margin: Decimal,
    ) -> dict[str, Decimal]:
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
        weighted_values: dict[str, Decimal] = {}
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
        targets: dict[str, Decimal] = {}
        for pos in positions:
            ratio = weighted_values[pos.symbol] / total_weighted
            targets[pos.symbol] = total_available_margin * ratio

        return targets

    def clear_cache(self) -> None:
        """清除波動率快取"""
        self._volatility_cache.clear()
```

**Step 4: 執行測試確認通過**

```bash
pytest tests/test_risk_calculator.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/core/risk_calculator.py tests/test_risk_calculator.py
git commit -m "feat: add risk calculator with volatility-based weights"
```

---

## Task 7: Margin Allocator 模組

**Files:**
- Create: `src/core/margin_allocator.py`
- Create: `tests/test_margin_allocator.py`

**Step 1: 寫 Margin Allocator 的 failing test**

```python
# tests/test_margin_allocator.py
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from src.core.margin_allocator import MarginAllocator, MarginAdjustmentPlan
from src.storage.models import Position, PositionSide


@pytest.fixture
def mock_config():
    """建立 mock 配置"""
    config = MagicMock()
    config.thresholds.min_adjustment_usdt = 50
    config.thresholds.min_deviation_pct = 5
    return config


@pytest.fixture
def mock_risk_calculator():
    """建立 mock 風險計算器"""
    calc = AsyncMock()
    calc.calculate_target_margins = AsyncMock(
        return_value={
            "BTC": Decimal("500"),
            "ETH": Decimal("300"),
        }
    )
    return calc


@pytest.fixture
def mock_client():
    """建立 mock API client"""
    client = AsyncMock()
    client.update_position_margin = AsyncMock(return_value=True)
    client.get_full_symbol = lambda s: f"t{s}F0:USTF0"
    return client


@pytest.fixture
def mock_db():
    """建立 mock 資料庫"""
    return AsyncMock()


@pytest.fixture
def allocator(mock_config, mock_risk_calculator, mock_client, mock_db):
    """建立 MarginAllocator"""
    return MarginAllocator(mock_config, mock_risk_calculator, mock_client, mock_db)


def test_calculate_adjustment_plan_increase():
    """測試計算需要增加保證金的調整計畫"""
    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("0.5"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("400"),  # 目標 500，需增加 100
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1.6"),
        ),
    ]

    targets = {"BTC": Decimal("500")}

    allocator = MarginAllocator(MagicMock(), AsyncMock(), AsyncMock(), AsyncMock())
    allocator.config.thresholds.min_adjustment_usdt = 50
    allocator.config.thresholds.min_deviation_pct = 5

    plans = allocator._calculate_adjustment_plans(positions, targets)

    assert len(plans) == 1
    assert plans[0].symbol == "BTC"
    assert plans[0].delta == Decimal("100")
    assert plans[0].is_increase is True


def test_calculate_adjustment_plan_decrease():
    """測試計算需要減少保證金的調整計畫"""
    positions = [
        Position(
            symbol="ETH",
            side=PositionSide.LONG,
            quantity=Decimal("10"),
            entry_price=Decimal("3000"),
            current_price=Decimal("3000"),
            margin=Decimal("400"),  # 目標 300，需減少 100
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1.33"),
        ),
    ]

    targets = {"ETH": Decimal("300")}

    allocator = MarginAllocator(MagicMock(), AsyncMock(), AsyncMock(), AsyncMock())
    allocator.config.thresholds.min_adjustment_usdt = 50
    allocator.config.thresholds.min_deviation_pct = 5

    plans = allocator._calculate_adjustment_plans(positions, targets)

    assert len(plans) == 1
    assert plans[0].symbol == "ETH"
    assert plans[0].delta == Decimal("-100")
    assert plans[0].is_increase is False


def test_calculate_adjustment_plan_below_threshold():
    """測試低於閾值不調整"""
    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("0.5"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("490"),  # 目標 500，只差 10（低於 50 閾值）
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1.96"),
        ),
    ]

    targets = {"BTC": Decimal("500")}

    allocator = MarginAllocator(MagicMock(), AsyncMock(), AsyncMock(), AsyncMock())
    allocator.config.thresholds.min_adjustment_usdt = 50
    allocator.config.thresholds.min_deviation_pct = 5

    plans = allocator._calculate_adjustment_plans(positions, targets)

    assert len(plans) == 0  # 不調整


def test_sort_plans_decrease_first():
    """測試排序：先減少再增加"""
    allocator = MarginAllocator(MagicMock(), AsyncMock(), AsyncMock(), AsyncMock())

    plans = [
        MarginAdjustmentPlan(
            symbol="BTC",
            current_margin=Decimal("400"),
            target_margin=Decimal("500"),
            delta=Decimal("100"),
        ),
        MarginAdjustmentPlan(
            symbol="ETH",
            current_margin=Decimal("400"),
            target_margin=Decimal("300"),
            delta=Decimal("-100"),
        ),
        MarginAdjustmentPlan(
            symbol="DOGE",
            current_margin=Decimal("200"),
            target_margin=Decimal("100"),
            delta=Decimal("-100"),
        ),
    ]

    sorted_plans = allocator._sort_plans(plans)

    # 減少的應該在前面
    assert sorted_plans[0].is_increase is False
    assert sorted_plans[1].is_increase is False
    assert sorted_plans[2].is_increase is True


@pytest.mark.asyncio
async def test_execute_rebalance(allocator, mock_client):
    """測試執行重平衡"""
    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("0.5"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("400"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1.6"),
        ),
        Position(
            symbol="ETH",
            side=PositionSide.LONG,
            quantity=Decimal("10"),
            entry_price=Decimal("3000"),
            current_price=Decimal("3000"),
            margin=Decimal("400"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1.33"),
        ),
    ]

    total_margin = Decimal("800")

    result = await allocator.execute_rebalance(positions, total_margin)

    assert result.success_count >= 0
    assert result.total_adjusted >= Decimal("0")
```

**Step 2: 執行測試確認失敗**

```bash
pytest tests/test_margin_allocator.py -v
```

Expected: FAIL

**Step 3: 實作 Margin Allocator**

```python
# src/core/margin_allocator.py
"""保證金分配模組：計算並執行保證金重分配"""

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

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
    adjustments: list[MarginAdjustment]


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
        positions: list[Position],
        targets: dict[str, Decimal],
    ) -> list[MarginAdjustmentPlan]:
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

            # 檢查是否超過閾值
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
        self, plans: list[MarginAdjustmentPlan]
    ) -> list[MarginAdjustmentPlan]:
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
        positions: list[Position],
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
        adjustments: list[MarginAdjustment] = []

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
        positions: list[Position],
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
```

**Step 4: 執行測試確認通過**

```bash
pytest tests/test_margin_allocator.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/core/margin_allocator.py tests/test_margin_allocator.py
git commit -m "feat: add margin allocator with rebalance logic"
```

---

## Task 8: Position Liquidator 模組

**Files:**
- Create: `src/core/position_liquidator.py`
- Create: `tests/test_position_liquidator.py`

**Step 1: 寫 Position Liquidator 的 failing test**

```python
# tests/test_position_liquidator.py
import pytest
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock
from datetime import datetime

from src.core.position_liquidator import PositionLiquidator, LiquidationPlan
from src.storage.models import Position, PositionSide


@pytest.fixture
def mock_config():
    """建立 mock 配置"""
    config = MagicMock()
    config.liquidation.enabled = True
    config.liquidation.dry_run = False
    config.liquidation.max_single_close_pct = 25
    config.liquidation.cooldown_seconds = 30
    config.liquidation.safety_margin_multiplier = 3.0
    config.position_priority = {"BTC": 100, "ETH": 90, "default": 50}
    config.get_position_priority = lambda s: config.position_priority.get(
        s, config.position_priority["default"]
    )
    return config


@pytest.fixture
def mock_client():
    """建立 mock API client"""
    client = AsyncMock()
    client.close_position = AsyncMock(return_value=True)
    client.get_full_symbol = lambda s: f"t{s}F0:USTF0"
    return client


@pytest.fixture
def mock_db():
    """建立 mock 資料庫"""
    return AsyncMock()


@pytest.fixture
def liquidator(mock_config, mock_client, mock_db):
    """建立 PositionLiquidator"""
    return PositionLiquidator(mock_config, mock_client, mock_db)


def test_calculate_margin_gap():
    """測試計算保證金缺口"""
    positions = [
        Position(
            symbol="BTC",
            side=PositionSide.LONG,
            quantity=Decimal("1"),
            entry_price=Decimal("50000"),
            current_price=Decimal("50000"),
            margin=Decimal("500"),  # 保證金率 1%
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1"),
        ),
    ]

    liquidator = PositionLiquidator(MagicMock(), AsyncMock(), AsyncMock())
    liquidator.config.liquidation.safety_margin_multiplier = 3.0

    # 最低安全保證金 = 名義價值 * 0.5% * 3 = 50000 * 0.005 * 3 = 750
    # 當前保證金 = 500
    # 可用餘額 = 100
    # 缺口 = 750 - 500 - 100 = 150
    gap = liquidator._calculate_margin_gap(positions, Decimal("100"))

    assert gap == Decimal("150")


def test_sort_positions_by_priority():
    """測試按優先級排序倉位"""
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
            symbol="DOGE",
            side=PositionSide.LONG,
            quantity=Decimal("10000"),
            entry_price=Decimal("0.1"),
            current_price=Decimal("0.1"),
            margin=Decimal("100"),
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("10"),
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

    config = MagicMock()
    config.position_priority = {"BTC": 100, "ETH": 90, "default": 50}
    config.get_position_priority = lambda s: config.position_priority.get(
        s, config.position_priority["default"]
    )

    liquidator = PositionLiquidator(config, AsyncMock(), AsyncMock())
    sorted_positions = liquidator._sort_by_priority(positions)

    # DOGE (50) < ETH (90) < BTC (100)
    assert sorted_positions[0].symbol == "DOGE"
    assert sorted_positions[1].symbol == "ETH"
    assert sorted_positions[2].symbol == "BTC"


def test_create_liquidation_plan():
    """測試建立減倉計畫"""
    position = Position(
        symbol="DOGE",
        side=PositionSide.LONG,
        quantity=Decimal("10000"),
        entry_price=Decimal("0.1"),
        current_price=Decimal("0.1"),
        margin=Decimal("100"),
        leverage=10,
        unrealized_pnl=Decimal("0"),
        margin_rate=Decimal("10"),
    )

    config = MagicMock()
    config.liquidation.max_single_close_pct = 25

    liquidator = PositionLiquidator(config, AsyncMock(), AsyncMock())
    plan = liquidator._create_liquidation_plan(position, Decimal("50"))

    # 最多平倉 25% = 2500
    # 但只需要釋放 50 USDT 的保證金
    assert plan.symbol == "DOGE"
    assert plan.close_quantity <= Decimal("2500")


@pytest.mark.asyncio
async def test_execute_liquidation_disabled(mock_config, mock_client, mock_db):
    """測試減倉功能停用"""
    mock_config.liquidation.enabled = False

    liquidator = PositionLiquidator(mock_config, mock_client, mock_db)

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
    ]

    result = await liquidator.execute_if_needed(positions, Decimal("0"))

    assert result.executed is False
    assert result.reason == "Liquidation disabled"


@pytest.mark.asyncio
async def test_execute_liquidation_dry_run(mock_config, mock_client, mock_db):
    """測試 dry run 模式"""
    mock_config.liquidation.dry_run = True

    liquidator = PositionLiquidator(mock_config, mock_client, mock_db)

    positions = [
        Position(
            symbol="DOGE",
            side=PositionSide.LONG,
            quantity=Decimal("10000"),
            entry_price=Decimal("0.1"),
            current_price=Decimal("0.1"),
            margin=Decimal("10"),  # 很低的保證金
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1"),
        ),
    ]

    result = await liquidator.execute_if_needed(positions, Decimal("0"))

    # dry run 模式不實際執行
    assert mock_client.close_position.called is False


@pytest.mark.asyncio
async def test_execute_liquidation_success(liquidator, mock_client):
    """測試成功執行減倉"""
    positions = [
        Position(
            symbol="DOGE",
            side=PositionSide.LONG,
            quantity=Decimal("10000"),
            entry_price=Decimal("0.1"),
            current_price=Decimal("0.1"),
            margin=Decimal("10"),  # 很低的保證金
            leverage=10,
            unrealized_pnl=Decimal("0"),
            margin_rate=Decimal("1"),
        ),
    ]

    result = await liquidator.execute_if_needed(positions, Decimal("0"))

    assert result.executed is True
    assert mock_client.close_position.called is True
```

**Step 2: 執行測試確認失敗**

```bash
pytest tests/test_position_liquidator.py -v
```

Expected: FAIL

**Step 3: 實作 Position Liquidator**

```python
# src/core/position_liquidator.py
"""倉位減倉模組：當保證金不足時自動減倉"""

import time
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from src.api.bitfinex_client import BitfinexClient
    from src.config_manager import Config
    from src.storage.database import Database

from src.storage.models import Position, Liquidation


@dataclass
class LiquidationPlan:
    """減倉計畫"""

    symbol: str
    side: str
    current_quantity: Decimal
    close_quantity: Decimal
    current_price: Decimal
    estimated_release: Decimal


@dataclass
class LiquidationResult:
    """減倉結果"""

    executed: bool
    reason: str
    plans: list[LiquidationPlan]
    success_count: int = 0
    fail_count: int = 0
    total_released: Decimal = Decimal("0")


class PositionLiquidator:
    """倉位減倉執行器"""

    MAINTENANCE_MARGIN_RATE = Decimal("0.005")  # 0.5%

    def __init__(
        self,
        config: "Config",
        client: "BitfinexClient",
        db: "Database",
    ):
        self.config = config
        self.client = client
        self.db = db
        self._last_liquidation_time: float = 0

    def _calculate_margin_gap(
        self,
        positions: list[Position],
        available_balance: Decimal,
    ) -> Decimal:
        """計算保證金缺口

        Args:
            positions: 倉位列表
            available_balance: 可用餘額

        Returns:
            保證金缺口（正數表示需要釋放的金額）
        """
        total_min_margin = Decimal("0")
        total_current_margin = Decimal("0")

        for pos in positions:
            # 最低安全保證金 = 名義價值 × 維持保證金率 × 安全係數
            min_margin = (
                pos.notional_value
                * self.MAINTENANCE_MARGIN_RATE
                * Decimal(str(self.config.liquidation.safety_margin_multiplier))
            )
            total_min_margin += min_margin
            total_current_margin += pos.margin

        # 缺口 = 所需最低保證金 - 當前保證金 - 可用餘額
        gap = total_min_margin - total_current_margin - available_balance

        return max(gap, Decimal("0"))

    def _sort_by_priority(self, positions: list[Position]) -> list[Position]:
        """按優先級排序倉位（低優先級在前）

        Args:
            positions: 倉位列表

        Returns:
            排序後的倉位列表
        """
        return sorted(
            positions,
            key=lambda p: self.config.get_position_priority(p.symbol),
        )

    def _create_liquidation_plan(
        self,
        position: Position,
        needed_release: Decimal,
    ) -> LiquidationPlan:
        """建立單一倉位的減倉計畫

        Args:
            position: 倉位
            needed_release: 需要釋放的保證金金額

        Returns:
            減倉計畫
        """
        # 計算最大可平倉數量（限制百分比）
        max_close_pct = Decimal(str(self.config.liquidation.max_single_close_pct)) / 100
        max_close_qty = position.quantity * max_close_pct

        # 計算釋放 needed_release 需要平多少倉
        # 估算：平倉會釋放的保證金 ≈ 平倉數量 / 總數量 × 當前保證金
        if position.margin > 0:
            qty_for_release = (
                needed_release / position.margin * position.quantity
            )
        else:
            qty_for_release = max_close_qty

        # 取較小值
        close_qty = min(max_close_qty, qty_for_release)

        # 估算釋放的保證金
        estimated_release = (close_qty / position.quantity) * position.margin

        return LiquidationPlan(
            symbol=position.symbol,
            side=position.side.value,
            current_quantity=position.quantity,
            close_quantity=close_qty,
            current_price=position.current_price,
            estimated_release=estimated_release,
        )

    def _check_cooldown(self) -> bool:
        """檢查是否在冷卻期內

        Returns:
            True 如果可以執行，False 如果在冷卻期內
        """
        elapsed = time.time() - self._last_liquidation_time
        return elapsed >= self.config.liquidation.cooldown_seconds

    async def execute_if_needed(
        self,
        positions: list[Position],
        available_balance: Decimal,
    ) -> LiquidationResult:
        """檢查並執行減倉（如果需要）

        Args:
            positions: 所有倉位
            available_balance: 可用餘額

        Returns:
            減倉結果
        """
        # 檢查是否啟用
        if not self.config.liquidation.enabled:
            return LiquidationResult(
                executed=False,
                reason="Liquidation disabled",
                plans=[],
            )

        # 檢查冷卻期
        if not self._check_cooldown():
            return LiquidationResult(
                executed=False,
                reason="In cooldown period",
                plans=[],
            )

        # 計算保證金缺口
        gap = self._calculate_margin_gap(positions, available_balance)

        if gap <= 0:
            return LiquidationResult(
                executed=False,
                reason="No margin gap",
                plans=[],
            )

        # 按優先級排序
        sorted_positions = self._sort_by_priority(positions)

        # 建立減倉計畫
        plans: list[LiquidationPlan] = []
        remaining_gap = gap

        for pos in sorted_positions:
            if remaining_gap <= 0:
                break

            plan = self._create_liquidation_plan(pos, remaining_gap)
            plans.append(plan)
            remaining_gap -= plan.estimated_release

        # dry run 模式
        if self.config.liquidation.dry_run:
            return LiquidationResult(
                executed=False,
                reason="Dry run mode",
                plans=plans,
            )

        # 執行減倉
        success_count = 0
        fail_count = 0
        total_released = Decimal("0")

        for plan in plans:
            full_symbol = self.client.get_full_symbol(plan.symbol)
            success = await self.client.close_position(
                symbol=full_symbol,
                side=Position(
                    symbol=plan.symbol,
                    side=plan.side,
                    quantity=plan.current_quantity,
                    entry_price=plan.current_price,
                    current_price=plan.current_price,
                    margin=Decimal("0"),
                    leverage=1,
                    unrealized_pnl=Decimal("0"),
                    margin_rate=Decimal("0"),
                ).side,
                quantity=plan.close_quantity,
            )

            if success:
                success_count += 1
                total_released += plan.estimated_release

                # 記錄到資料庫
                liq = Liquidation(
                    timestamp=datetime.now(),
                    symbol=plan.symbol,
                    side=Position(
                        symbol=plan.symbol,
                        side=plan.side,
                        quantity=plan.current_quantity,
                        entry_price=plan.current_price,
                        current_price=plan.current_price,
                        margin=Decimal("0"),
                        leverage=1,
                        unrealized_pnl=Decimal("0"),
                        margin_rate=Decimal("0"),
                    ).side,
                    quantity=plan.close_quantity,
                    price=plan.current_price,
                    released_margin=plan.estimated_release,
                    reason=f"Margin gap: {gap}",
                )
                await self.db.save_liquidation(liq)
            else:
                fail_count += 1

        # 更新最後執行時間
        self._last_liquidation_time = time.time()

        return LiquidationResult(
            executed=True,
            reason=f"Executed {success_count} liquidations",
            plans=plans,
            success_count=success_count,
            fail_count=fail_count,
            total_released=total_released,
        )
```

**Step 4: 執行測試確認通過**

```bash
pytest tests/test_position_liquidator.py -v
```

Expected: All tests PASS

**Step 5: Commit**

```bash
git add src/core/position_liquidator.py tests/test_position_liquidator.py
git commit -m "feat: add position liquidator with priority-based closing"
```

---

## Task 9-14: 其他模組（摘要）

由於篇幅限制，以下模組提供摘要規格：

### Task 9: Telegram Notifier (`src/notifier/telegram_bot.py`)
- 使用 python-telegram-bot 庫
- 支援訊息類型：常規調整、緊急調整、減倉警告、減倉完成、定時報告、錯誤通知
- 實作 `send_message()`, `send_adjustment_report()`, `send_liquidation_alert()`, `send_daily_report()`

### Task 10: Scheduler (`src/scheduler/poll_scheduler.py`)
- 使用 asyncio 實作定時輪詢
- 支援可配置的輪詢間隔
- 實作 `start()`, `stop()`, `run_once()`

### Task 11: Event Detector (`src/scheduler/event_detector.py`)
- 監控倉位保證金率
- 偵測價格劇烈波動
- 觸發緊急重平衡
- 實作 `check_emergency_conditions()`, `on_price_update()`

### Task 12: WebSocket Client (`src/api/bitfinex_ws.py`)
- Bitfinex WebSocket 連線管理
- 訂閱價格更新
- 自動重連機制
- 實作 `connect()`, `subscribe()`, `on_message()`

### Task 13: Main Entry (`src/main.py`)
- 初始化所有模組
- 啟動前檢查
- 主迴圈協調
- 優雅關閉

### Task 14: Integration Tests
- 端對端測試
- 模擬完整流程

---

## 執行選項

**計畫已完成並儲存至 `spec-all.md`。兩種執行方式：**

**1. Subagent-Driven（此 session）** - 我在這個 session 中逐 task 派發 subagent 執行，每個 task 完成後 review

**2. Parallel Session（另開 session）** - 你開新的 Claude Code session，使用 superpowers:executing-plans 批次執行

**選擇哪種方式？**
