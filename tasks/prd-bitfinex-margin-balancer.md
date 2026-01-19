# PRD: Bitfinex Margin Balancer

## Introduction

建立一個獨立 Python 服務，在 Bitfinex 逐倉（Isolated Margin）模式下模擬全倉（Cross Margin）保證金行為。此服務專為內部團隊設計，支援 20+ 倉位的風險權重動態分配與自動減倉，以最大化資金使用效率並降低爆倉風險。

**核心概念：** 逐倉模式下，每個倉位的保證金獨立管理，當某倉位虧損時無法自動使用其他倉位的閒置保證金。本服務透過定時重平衡和緊急事件監控，動態調整各倉位保證金，實現類似全倉的風險分攤效果。

## Goals

- 自動根據風險權重（波動率）分配各倉位保證金
- 當倉位保證金率過低時自動緊急補充
- 當整體保證金不足時按優先級自動減倉
- 透過 Telegram 即時通知重要操作和異常狀況
- 提供 Dry Run 模式，可執行讀取操作但不執行寫入操作（調整保證金、平倉）
- 記錄所有操作歷史供事後分析

## User Stories

### US-001: 專案初始化與基礎結構

**Description:** As a 開發者，I want 建立標準化的 Python 專案結構 so that 後續開發有清晰的模組劃分。

**Acceptance Criteria:**
- [ ] 建立目錄結構：`config/`, `src/`, `tests/`, `logs/`, `data/`
- [ ] 建立 `pyproject.toml` 包含所有依賴（aiohttp, websockets, pydantic, etc.）
- [ ] 建立 `requirements.txt` 供快速安裝
- [ ] 建立 `config/config.example.yaml` 範例配置檔
- [ ] 所有模組目錄包含 `__init__.py`
- [ ] Git repository 初始化完成

---

### US-002: Config Manager 模組

**Description:** As a 服務，I want 從 YAML 配置檔載入設定並支援環境變數替換 so that 敏感資訊（API keys）不需硬編碼。

**Acceptance Criteria:**
- [ ] 實作 `src/config_manager.py`
- [ ] 使用 Pydantic 定義配置模型（BitfinexConfig, TelegramConfig, MonitorConfig 等）
- [ ] 支援 `${ENV_VAR}` 語法的環境變數替換
- [ ] `get_risk_weight(symbol)` 回傳配置的權重或 None（由 RiskCalculator 自動計算）
- [ ] `get_position_priority(symbol)` 回傳優先級，未配置則使用 default 值
- [ ] 測試：`tests/test_config_manager.py` 全部通過
- [ ] Typecheck passes

---

### US-003: Data Models 模組

**Description:** As a 開發者，I want 定義統一的資料模型 so that 各模組間的資料傳遞有型別保障。

**Acceptance Criteria:**
- [ ] 實作 `src/storage/models.py`
- [ ] `Position` 模型包含：symbol, side, quantity, entry_price, current_price, margin, leverage, unrealized_pnl, margin_rate
- [ ] `Position` 提供 computed fields：`notional_value`, `is_profitable`
- [ ] `MarginAdjustment` 模型記錄保證金調整歷史
- [ ] `Liquidation` 模型記錄減倉歷史
- [ ] `AccountSnapshot` 模型記錄帳戶快照
- [ ] 定義 Enums：`PositionSide`, `AdjustmentDirection`, `TriggerType`
- [ ] 測試：`tests/test_models.py` 全部通過
- [ ] Typecheck passes

---

### US-004: Database 模組

**Description:** As a 服務，I want 將操作歷史儲存到 SQLite so that 可以事後查詢和分析。

**Acceptance Criteria:**
- [ ] 實作 `src/storage/database.py`
- [ ] 使用 aiosqlite 實作非同步操作
- [ ] `initialize()` 自動建立表：margin_adjustments, liquidations, account_snapshots
- [ ] 實作 CRUD：`save_margin_adjustment()`, `get_margin_adjustments()`, `save_liquidation()`, `get_liquidations()`, `save_account_snapshot()`, `get_account_snapshots()`
- [ ] `get_daily_stats(date)` 回傳指定日期的調整/減倉統計
- [ ] 測試：`tests/test_database.py` 全部通過
- [ ] Typecheck passes

---

### US-005: Bitfinex REST API Client 模組

**Description:** As a 服務，I want 透過 Bitfinex REST API 取得帳戶資訊並執行操作 so that 可以監控和管理倉位。

**Acceptance Criteria:**
- [ ] 實作 `src/api/bitfinex_client.py`
- [ ] 正確實作 API 簽名（HMAC SHA384）
- [ ] `get_positions()` 取得所有衍生品倉位，解析為 Position 模型
- [ ] `get_derivatives_balance()` 取得衍生品錢包可用餘額
- [ ] `update_position_margin(symbol, delta)` 調整倉位保證金
- [ ] `close_position(symbol, side, quantity)` 市價平倉
- [ ] `get_candles(symbol, timeframe, limit)` 取得 K 線資料（用於波動率計算）
- [ ] `get_full_symbol(symbol)` 將 "BTC" 轉換為 "tBTCF0:USTF0"
- [ ] **錯誤重試機制：** API 調用失敗時自動重試，最多 10 次，使用指數退避（exponential backoff）
- [ ] **重試失敗報警：** 連續 10 次重試失敗後透過 Telegram 發送報警通知
- [ ] 測試：`tests/test_bitfinex_client.py` 全部通過（使用 mock）
- [ ] Typecheck passes

---

### US-006: Risk Calculator 模組

**Description:** As a 服務，I want 根據波動率計算各幣種的風險權重 so that 保證金分配能反映實際風險。

**Acceptance Criteria:**
- [ ] 實作 `src/core/risk_calculator.py`
- [ ] `_calculate_volatility(prices)` 計算價格序列的波動率
- [ ] `get_risk_weight(symbol)` 優先回傳配置值，否則根據歷史波動率自動計算
- [ ] 自動計算的權重以 BTC 波動率為基準正規化
- [ ] `calculate_target_margins(positions, total_margin)` 根據風險權重計算各倉位目標保證金
- [ ] 波動率快取機制，`clear_cache()` 可清除
- [ ] **動態計算週期：** 正常情況每 `volatility_update_hours` 小時更新；當偵測到波動劇烈（價格變動超過 `price_spike_pct`）時，縮短為每 10 分鐘更新
- [ ] 測試：`tests/test_risk_calculator.py` 全部通過
- [ ] Typecheck passes

---

### US-007: Margin Allocator 模組

**Description:** As a 服務，I want 計算並執行保證金重分配 so that 各倉位保證金符合風險權重。

**Acceptance Criteria:**
- [ ] 實作 `src/core/margin_allocator.py`
- [ ] `_calculate_adjustment_plans()` 計算需要調整的倉位，過濾低於閾值的調整
- [ ] 閾值檢查：min_adjustment_usdt（絕對值）和 min_deviation_pct（百分比）
- [ ] `_sort_plans()` 排序：先執行減少（釋放資金），再執行增加（使用資金）
- [ ] `execute_rebalance()` 執行重平衡，回傳 RebalanceResult
- [ ] `emergency_rebalance()` 針對單一危險倉位緊急補充保證金
- [ ] 每次調整都記錄到資料庫
- [ ] 測試：`tests/test_margin_allocator.py` 全部通過
- [ ] Typecheck passes

---

### US-008: Position Liquidator 模組

**Description:** As a 服務，I want 當整體保證金不足時自動減倉 so that 避免被交易所強制清算。

**Acceptance Criteria:**
- [ ] 實作 `src/core/position_liquidator.py`
- [ ] `_calculate_margin_gap()` 計算保證金缺口
- [ ] `_sort_by_priority()` 按配置的優先級排序（低優先級先減倉）
- [ ] `_create_liquidation_plan()` 建立減倉計畫，限制單次最大平倉百分比
- [ ] `execute_if_needed()` 檢查並執行減倉
- [ ] 支援 `dry_run` 模式：計算計畫但不實際執行
- [ ] 支援冷卻期：連續減倉間隔限制
- [ ] 每次減倉記錄到資料庫
- [ ] 測試：`tests/test_position_liquidator.py` 全部通過
- [ ] Typecheck passes

---

### US-009: Telegram Notifier 模組

**Description:** As a 團隊成員，I want 收到 Telegram 通知 so that 能即時掌握系統狀態和異常。

**Acceptance Criteria:**
- [ ] 實作 `src/notifier/telegram_bot.py`
- [ ] 使用 python-telegram-bot 庫
- [ ] `send_message(text)` 發送一般訊息
- [ ] `send_adjustment_report(result)` 發送保證金調整報告
- [ ] `send_liquidation_alert(result)` 發送減倉警告
- [ ] `send_daily_report(stats)` 發送每日統計報告
- [ ] `send_api_error_alert(error, retry_count)` 發送 API 重試失敗報警
- [ ] `send_account_margin_warning(margin_rate)` 發送帳戶保證金率預警
- [ ] 支援 `enabled` 開關
- [ ] 測試：`tests/test_telegram_bot.py` 全部通過（使用 mock）
- [ ] Typecheck passes

---

### US-010: Poll Scheduler 模組

**Description:** As a 服務，I want 定時執行保證金重平衡 so that 倉位保持最佳配置。

**Acceptance Criteria:**
- [ ] 實作 `src/scheduler/poll_scheduler.py`
- [ ] 使用 asyncio 實作定時輪詢
- [ ] `start()` 開始定時執行
- [ ] `stop()` 優雅停止
- [ ] `run_once()` 執行單次重平衡（用於測試）
- [ ] 輪詢間隔可配置（`poll_interval_sec`）
- [ ] 測試：`tests/test_poll_scheduler.py` 全部通過
- [ ] Typecheck passes

---

### US-011: Event Detector 模組

**Description:** As a 服務，I want 監控緊急事件 so that 能即時反應危險狀況。

**Acceptance Criteria:**
- [ ] 實作 `src/scheduler/event_detector.py`
- [ ] `check_emergency_conditions(positions)` 檢查是否有倉位低於 emergency_margin_rate
- [ ] `on_price_update(symbol, price, prev_price)` 檢查價格劇烈波動（超過 price_spike_pct）
- [ ] **帳戶保證金率監控：** `check_account_margin_rate(total_equity, total_margin)` 當整體帳戶保證金率低於 `account_margin_rate_warning` 時發送提前警告
- [ ] 觸發緊急重平衡時通知 Telegram
- [ ] 測試：`tests/test_event_detector.py` 全部通過
- [ ] Typecheck passes

---

### US-012: WebSocket Client 模組

**Description:** As a 服務，I want 透過 WebSocket 即時接收價格更新 so that 能快速偵測價格異常。

**Acceptance Criteria:**
- [ ] 實作 `src/api/bitfinex_ws.py`
- [ ] `connect()` 建立 WebSocket 連線
- [ ] `subscribe(symbols)` 訂閱價格更新
- [ ] **智慧訂閱：** 只訂閱保證金率較低的高風險倉位（低於 `emergency_margin_rate * 2`），而非所有持倉幣種
- [ ] `update_subscriptions(positions)` 根據當前倉位風險動態調整訂閱列表
- [ ] `on_message(callback)` 註冊訊息回調
- [ ] 自動重連機制（斷線後自動重連）
- [ ] 測試：`tests/test_bitfinex_ws.py` 全部通過（使用 mock）
- [ ] Typecheck passes

---

### US-013: Main Entry 模組

**Description:** As a 運維人員，I want 一個統一的進入點 so that 可以簡單啟動和停止服務。

**Acceptance Criteria:**
- [ ] 實作 `src/main.py`
- [ ] 載入配置檔
- [ ] 初始化所有模組（Database, BitfinexClient, RiskCalculator, etc.）
- [ ] 啟動前檢查（API 連線、配置驗證）
- [ ] 主迴圈：Poll Scheduler + WebSocket 監控
- [ ] 優雅關閉：捕捉 SIGINT/SIGTERM，清理資源
- [ ] 支援 `--dry-run` 命令列參數
- [ ] 支援 `--config` 指定配置檔路徑
- [ ] Typecheck passes

---

### US-014: Integration Tests

**Description:** As a 開發者，I want 端對端整合測試 so that 確保各模組協同運作正常。

**Acceptance Criteria:**
- [ ] 實作 `tests/test_integration.py`
- [ ] 測試完整流程：取得倉位 → 計算目標 → 執行調整 → 記錄資料庫
- [ ] 測試緊急重平衡流程
- [ ] 測試減倉流程
- [ ] 使用 mock API 避免實際交易
- [ ] 所有整合測試通過

---

## Functional Requirements

- FR-1: 服務啟動時從 YAML 配置檔載入設定，敏感資訊透過環境變數注入
- FR-2: 每 `poll_interval_sec` 秒執行一次保證金重平衡檢查
- FR-3: 重平衡時，先減少（釋放資金），再增加（使用資金）
- FR-4: 調整金額低於 `min_adjustment_usdt` 或偏差低於 `min_deviation_pct` 時不執行調整
- FR-5: 當倉位保證金率低於 `emergency_margin_rate` 時觸發緊急重平衡
- FR-6: 當價格變動超過 `price_spike_pct` 時觸發緊急檢查
- FR-7: 減倉按優先級排序，`position_priority` 數值越低越先被減倉
- FR-8: 單次減倉不超過倉位的 `max_single_close_pct`
- FR-9: 減倉後需等待 `cooldown_seconds` 才能再次減倉
- FR-10: `dry_run` 模式下，執行所有讀取操作但不執行寫入操作（保證金調整、平倉）
- FR-11: 所有保證金調整和減倉操作記錄到 SQLite 資料庫
- FR-12: 透過 Telegram 發送：調整報告、減倉警告、每日統計、錯誤通知
- FR-13: WebSocket 斷線後自動重連
- FR-14: 服務收到 SIGINT/SIGTERM 時優雅關閉，完成進行中的操作
- FR-15: **動態波動率計算：** 正常情況每 `volatility_update_hours` 小時更新；偵測到劇烈波動時縮短為每 10 分鐘更新
- FR-16: **智慧 WebSocket 訂閱：** 只訂閱保證金率較低的高風險倉位（低於 `emergency_margin_rate * 2`），定時重新評估訂閱列表
- FR-17: **API 錯誤重試：** 所有 API 調用失敗時自動重試，最多 10 次，使用指數退避策略
- FR-18: **重試失敗報警：** 連續重試失敗達上限時透過 Telegram 發送報警通知
- FR-19: **帳戶保證金率提前警告：** 當整體帳戶保證金率低於 `account_margin_rate_warning` 時發送 Telegram 預警

## Non-Goals

- 不支援多帳戶管理（僅單一 Bitfinex 帳戶）
- 不支援非衍生品倉位（Spot、Margin）
- 不提供 Web UI 或 REST API 介面
- 不實作交易策略或自動開倉
- 不支援其他交易所
- 不做歷史回測功能

## Technical Considerations

### Tech Stack
- **Runtime:** Python 3.11+
- **Async Framework:** asyncio
- **HTTP Client:** aiohttp
- **WebSocket:** websockets
- **Config:** PyYAML + pydantic + pydantic-settings
- **Database:** aiosqlite (SQLite)
- **Notification:** python-telegram-bot
- **Math:** numpy

### Architecture
```
┌─────────────────────────────────────────────────────────┐
│                        main.py                          │
│         (初始化、協調、優雅關閉)                          │
├─────────────┬───────────────────────┬───────────────────┤
│ Scheduler   │                       │ WebSocket Client  │
│ (定時輪詢)   │                       │ (即時監控)         │
├─────────────┴───────────────────────┴───────────────────┤
│                    Event Detector                       │
│              (緊急狀況偵測)                              │
├─────────────────────────────────────────────────────────┤
│     Risk Calculator    │      Margin Allocator          │
│     (風險權重計算)       │      (保證金分配)              │
├────────────────────────┴────────────────────────────────┤
│                 Position Liquidator                     │
│                   (自動減倉)                             │
├─────────────────────────────────────────────────────────┤
│   Bitfinex REST Client   │    Telegram Notifier         │
│     (API 操作)            │     (通知推送)               │
├──────────────────────────┴──────────────────────────────┤
│                      Database                           │
│                  (歷史記錄儲存)                          │
└─────────────────────────────────────────────────────────┘
```

### Dry Run Mode 行為
| 操作類型 | Dry Run 行為 |
|---------|-------------|
| 取得倉位 | 正常執行 |
| 取得餘額 | 正常執行 |
| 取得 K 線 | 正常執行 |
| 調整保證金 | 只記錄日誌，不實際執行 |
| 平倉 | 只記錄日誌，不實際執行 |

## Success Metrics

- 服務可持續運行 24 小時以上無崩潰
- 定時重平衡延遲不超過配置間隔的 10%
- 緊急事件偵測到執行重平衡延遲不超過 5 秒
- 所有操作正確記錄到資料庫
- Telegram 通知延遲不超過 10 秒
- 測試覆蓋率達 80% 以上

## Open Questions

*所有問題已解決，無待決事項。*
