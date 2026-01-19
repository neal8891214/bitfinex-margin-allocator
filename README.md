# Bitfinex Margin Balancer

Python 服務，在 Bitfinex 逐倉模式下模擬全倉保證金行為。透過定時重平衡和緊急事件監控，動態調整各倉位保證金，實現風險分攤。

## 功能特點

- **風險權重分配**：根據波動率自動計算各幣種風險權重，或手動配置
- **定時重平衡**：定期檢查並調整各倉位保證金至目標值
- **緊急事件監控**：WebSocket 即時監控價格，偵測低保證金率或價格劇烈波動
- **自動減倉**：當整體保證金不足時，按優先級自動平倉釋放資金
- **Telegram 通知**：重要事件即時推送
- **Dry-run 模式**：測試配置而不執行實際交易

## 環境需求

- Python 3.9+
- Bitfinex 帳戶與 API Key（需啟用衍生品交易權限）
- （選用）Telegram Bot Token

## Quickstart

### 1. 安裝依賴

```bash
# Clone 專案
git clone https://github.com/johnliu33/bitfinex-margin-allocator.git
cd bitfinex-margin-allocator

# 建立虛擬環境（建議）
python -m venv venv
source venv/bin/activate  # Windows: venv\Scripts\activate

# 安裝套件
pip install -e ".[dev]"
```

### 2. 設定環境變數

```bash
export BITFINEX_API_KEY="your-api-key"
export BITFINEX_API_SECRET="your-api-secret"

# 選用：Telegram 通知
export TELEGRAM_BOT_TOKEN="your-bot-token"
export TELEGRAM_CHAT_ID="your-chat-id"
```

### 3. 建立配置檔

```bash
cp config/config.example.yaml config/config.yaml
```

依需求編輯 `config/config.yaml`，主要配置項目：

| 區塊 | 設定 | 說明 |
|------|------|------|
| `monitor.poll_interval_sec` | 60 | 重平衡輪詢間隔（秒） |
| `thresholds.emergency_margin_rate` | 2.0 | 緊急保證金率閾值 |
| `thresholds.price_spike_pct` | 3.0 | 價格劇烈波動閾值（%） |
| `risk_weights` | - | 手動指定幣種風險權重 |
| `position_priority` | - | 減倉優先級（數字越大越不會被平） |
| `liquidation.dry_run` | true | 減倉 dry-run 模式 |

### 4. 執行服務

```bash
# Dry-run 模式（不執行實際交易，建議先用此模式測試）
python -m src.main --config config/config.yaml --dry-run

# 正式執行
python -m src.main --config config/config.yaml
```

### 5. 執行測試

```bash
# 執行全部測試
pytest tests/ -v

# 執行測試並顯示覆蓋率
pytest tests/ -v --cov=src

# 類型檢查
python -m mypy src/
```

## 架構

```
main.py (entry point)
    ├── PollScheduler (定時輪詢)
    │       └── MarginAllocator → RiskCalculator
    │                          → PositionLiquidator
    ├── BitfinexWebSocket (即時價格監控)
    │       └── EventDetector (緊急事件偵測)
    ├── BitfinexClient (REST API)
    ├── TelegramNotifier (通知)
    └── Database (SQLite 歷史記錄)
```

## 運作流程

1. **定時重平衡**：PollScheduler 每 N 秒觸發 → 取得倉位 → RiskCalculator 計算目標保證金 → MarginAllocator 執行調整
2. **緊急處理**：WebSocket 收到價格更新 → EventDetector 檢查緊急條件 → 觸發 emergency_rebalance 或減倉
3. **記錄與通知**：所有調整記錄到 SQLite，重要事件通知 Telegram

## License

MIT
