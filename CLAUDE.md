# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Bitfinex Margin Balancer - Python 服務，在 Bitfinex 逐倉模式下模擬全倉保證金行為。透過定時重平衡和緊急事件監控，動態調整各倉位保證金，實現風險分攤。

## Tech Stack

- Python 3.11+, asyncio
- aiohttp (REST API), websockets (WebSocket)
- pydantic (config/models), aiosqlite (SQLite)
- python-telegram-bot (notifications)
- pytest, pytest-asyncio (testing)

## Commands

```bash
# Install dependencies
pip install -e ".[dev]"

# Run tests
pytest tests/ -v

# Run single test file
pytest tests/test_config_manager.py -v

# Run single test
pytest tests/test_config_manager.py::test_load_config_from_yaml -v

# Type check
python -m mypy src/

# Run service
python -m src.main --config config/config.yaml

# Run in dry-run mode (no writes to Bitfinex)
python -m src.main --config config/config.yaml --dry-run
```

## Architecture

```
main.py (entry point, signal handling)
    ├── PollScheduler (定時輪詢)
    │       └── MarginAllocator → RiskCalculator
    │                          → PositionLiquidator
    ├── BitfinexWebSocket (即時價格監控)
    │       └── EventDetector (緊急事件偵測)
    ├── BitfinexClient (REST API)
    ├── TelegramNotifier (通知)
    └── Database (SQLite 歷史記錄)
```

**Data Flow:**
1. PollScheduler 每 N 秒觸發 → 取得倉位 → RiskCalculator 計算目標保證金 → MarginAllocator 執行調整
2. WebSocket 收到價格更新 → EventDetector 檢查緊急條件 → 觸發 emergency_rebalance 或減倉
3. 所有調整記錄到 Database，重要事件通知 Telegram

## Key Modules

| Module | File | Purpose |
|--------|------|---------|
| Config | `src/config_manager.py` | YAML 配置載入，支援 `${ENV_VAR}` 替換 |
| Models | `src/storage/models.py` | Position, MarginAdjustment, Liquidation 資料模型 |
| API Client | `src/api/bitfinex_client.py` | Bitfinex REST API（含重試機制） |
| WebSocket | `src/api/bitfinex_ws.py` | 即時價格監控，智慧訂閱高風險倉位 |
| Risk | `src/core/risk_calculator.py` | 波動率計算，風險權重分配 |
| Allocator | `src/core/margin_allocator.py` | 保證金重平衡邏輯 |
| Liquidator | `src/core/position_liquidator.py` | 自動減倉（按優先級） |
| Scheduler | `src/scheduler/poll_scheduler.py` | 定時輪詢重平衡 |
| Detector | `src/scheduler/event_detector.py` | 緊急事件偵測（低保證金率、價格劇烈波動） |

## Configuration

複製 `config/config.example.yaml` 為 `config/config.yaml`，設定環境變數：
- `BITFINEX_API_KEY`, `BITFINEX_API_SECRET`
- `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`

## Testing

所有測試使用 mock 避免實際 API 調用。測試檔案對應模組名稱：`tests/test_<module>.py`

```bash
# 執行全部測試並顯示覆蓋率
pytest tests/ -v --cov=src

# 執行特定測試類別
pytest tests/test_margin_allocator.py::TestExecuteRebalance -v
```

## Implementation Reference

詳細實作規格見 `spec-all.md`，包含每個模組的完整程式碼範例和測試案例。
