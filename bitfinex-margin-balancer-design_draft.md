# Bitfinex 逐倉模擬全倉保證金管理工具設計文件

> 建立日期：2026-01-19

## 1. 專案概述

### 1.1 背景

Bitfinex 衍生品交易平台僅支援「逐倉保證金」（Isolated Margin）模式，但現有交易策略皆基於「全倉保證金」（Cross Margin）模式開發。本工具透過自動化保證金重分配，使逐倉模式達到全倉模式的效果。

### 1.2 目標

- 在 Bitfinex 逐倉模式下模擬全倉保證金行為
- 支援 20+ 個倉位的大規模管理
- 按風險權重動態分配保證金
- 保證金不足時自動減倉

### 1.3 設計決策

| 決策項目 | 選擇 |
|---------|------|
| 架構 | 模組化獨立服務 |
| 風險分配 | 按波動率風險權重 |
| 波動率來源 | 自動計算 + 手動覆蓋 |
| 執行頻率 | 定時輪詢 + 緊急事件觸發 |
| 保證金不足處理 | 自動減倉（可配置優先級） |
| 通知方式 | Telegram Bot |
| 歷史記錄 | SQLite 完整記錄 |

---

## 2. 系統架構

```
┌─────────────────────────────────────────────────────────────┐
│                    Margin Balancer Service                   │
├─────────────────────────────────────────────────────────────┤
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │   Config    │  │  Scheduler  │  │   Event Detector    │  │
│  │   Manager   │  │  (定時輪詢)  │  │   (緊急事件監控)     │  │
│  └──────┬──────┘  └──────┬──────┘  └──────────┬──────────┘  │
│         │                │                     │             │
│         ▼                ▼                     ▼             │
│  ┌────────────────────────────────────────────────────────┐ │
│  │                  Core Engine (核心引擎)                  │ │
│  │  ┌────────────┐ ┌────────────┐ ┌────────────────────┐  │ │
│  │  │ Risk Calc  │ │  Margin    │ │  Position          │  │ │
│  │  │ (風險計算)  │ │ Allocator  │ │  Liquidator        │  │ │
│  │  │            │ │ (保證金分配)│ │  (減倉執行器)       │  │ │
│  │  └────────────┘ └────────────┘ └────────────────────┘  │ │
│  └────────────────────────────────────────────────────────┘ │
│         │                │                     │             │
│         ▼                ▼                     ▼             │
│  ┌─────────────┐  ┌─────────────┐  ┌─────────────────────┐  │
│  │  Bitfinex   │  │  Notifier   │  │   History Store     │  │
│  │  API Client │  │ (Telegram)  │  │   (SQLite/記錄)     │  │
│  └─────────────┘  └─────────────┘  └─────────────────────┘  │
└─────────────────────────────────────────────────────────────┘
```

### 2.1 模組職責

| 模組 | 職責 |
|------|------|
| Config Manager | 載入配置（風險權重、優先級、閾值） |
| Scheduler | 定時輪詢（每 1-2 分鐘） |
| Event Detector | 監控 WebSocket 價格流，偵測緊急事件 |
| Risk Calculator | 計算各幣種波動率與風險權重 |
| Margin Allocator | 保證金分配決策與執行 |
| Position Liquidator | 減倉邏輯與執行 |
| Bitfinex API Client | 封裝所有 Bitfinex API 操作 |
| Notifier | Telegram 推送通知 |
| History Store | SQLite 記錄所有操作 |

---

## 3. 風險計算與保證金分配

### 3.1 風險權重計算

```python
# 每個幣種的風險權重 = 基礎波動率 × 手動調整係數
risk_weight[symbol] = base_volatility[symbol] × manual_multiplier[symbol]
```

**基礎波動率計算**（自動）：
- 使用過去 7 天的價格標準差（可配置天數）
- 每小時自動更新一次波動率數據
- 從 Bitfinex K 線 API 取得歷史價格

**手動調整係數**（配置檔）：
```yaml
risk_weights:
  BTC: 1.0      # 基準
  ETH: 1.2      # 比 BTC 多配 20% 保證金
  DOGE: 2.0     # 高波動，配 2 倍保證金
  XRP: 0.8      # 較穩定，少配 20%
```

### 3.2 保證金分配公式

```
目標保證金[i] = 總可用保證金 × (倉位價值[i] × 風險權重[i]) / Σ(倉位價值 × 風險權重)
```

**範例**：
- 總可用保證金：10,000 USDT
- BTC 倉位 $50,000，權重 1.0 → 加權值 = 50,000
- ETH 倉位 $30,000，權重 1.2 → 加權值 = 36,000
- DOGE 倉位 $20,000，權重 2.0 → 加權值 = 40,000
- 加權總和 = 126,000
- BTC 目標保證金 = 10,000 × (50,000/126,000) ≈ 3,968 USDT
- ETH 目標保證金 = 10,000 × (36,000/126,000) ≈ 2,857 USDT
- DOGE 目標保證金 = 10,000 × (40,000/126,000) ≈ 3,175 USDT

---

## 4. 監控與觸發機制

### 4.1 雙軌觸發策略

```
┌─────────────────────────────────────────────────────────┐
│                     觸發來源                             │
├────────────────────────┬────────────────────────────────┤
│     定時輪詢 (常規)     │       緊急事件 (即時)           │
│    每 60-120 秒執行     │      WebSocket 價格監控         │
├────────────────────────┼────────────────────────────────┤
│  • 重新計算所有倉位      │  觸發條件：                     │
│    的目標保證金          │  • 任一倉位保證金率 < 2%        │
│  • 執行差異超過閾值      │  • 價格 1 分鐘內波動 > 3%       │
│    的調整               │  • 帳戶總保證金率 < 3%          │
│  • 更新波動率數據        │                                │
│    (每小時)             │  → 立即執行保證金重分配         │
└────────────────────────┴────────────────────────────────┘
```

### 4.2 調整閾值

```yaml
thresholds:
  min_adjustment: 50        # 最小調整金額 (USDT)，低於不動
  min_deviation_pct: 5      # 偏離目標 <5% 不調整
  emergency_margin_rate: 2  # 保證金率 <2% 觸發緊急
  price_spike_pct: 3        # 1 分鐘價格波動 >3% 觸發緊急
```

### 4.3 調整執行流程

1. 計算每個倉位的「目標保證金」與「當前保證金」差異
2. 過濾掉差異小於閾值的倉位
3. 排序：先處理「需要減少」的倉位（釋放資金）
4. 再處理「需要增加」的倉位（使用釋放的資金）
5. 透過 Bitfinex API 逐一調整
6. 記錄每筆調整到 History Store

---

## 5. 自動減倉機制

### 5.1 觸發條件

```
當「可用保證金」無法滿足「所有倉位的最低安全保證金」時觸發
最低安全保證金 = 維持保證金 (0.5%) × 安全係數 (預設 3 倍 = 1.5%)
```

### 5.2 優先級配置

```yaml
position_priority:
  # 數字越大，優先級越高（越不會被平）
  BTC: 100      # 核心持倉，最後才平
  ETH: 90
  SOL: 70
  DOGE: 30      # 投機倉位，優先平掉
  default: 50   # 未配置的幣種預設值
```

### 5.3 減倉執行邏輯

1. 計算保證金缺口 = 所需保證金 - 可用保證金
2. 將所有倉位按優先級排序（低優先級在前）
3. 從最低優先級開始，計算平倉可釋放的保證金
   - 優先「部分平倉」而非「全部平倉」
   - 每次減倉 25% 的倉位大小，直到補足缺口
4. 執行減倉前發送 Telegram 警告（可配置等待確認）
5. 執行市價平倉，記錄到歷史

### 5.4 安全機制

```yaml
liquidation:
  enabled: true              # 總開關
  require_confirmation: false # true = 等待 Telegram 確認才執行
  max_single_close_pct: 25   # 單次最多平倉該倉位的 25%
  cooldown_seconds: 30       # 兩次減倉間隔至少 30 秒
  dry_run: true              # 測試模式：只通知不執行
```

---

## 6. 通知與歷史記錄

### 6.1 Telegram 通知類型

| 類型 | 內容 |
|------|------|
| 🔄 常規調整 | 調整了 N 個倉位的保證金，總移動 X USDT |
| ⚠️ 緊急調整 | [幣種] 保證金率過低，已緊急補充 |
| 🔴 減倉警告 | 保證金不足，準備平倉 [幣種] [數量] |
| ✅ 減倉完成 | 已平倉 [幣種] [數量]，釋放 X USDT |
| 📊 定時報告 | 每日/每小時帳戶狀態摘要 |
| ❌ 錯誤通知 | API 錯誤、連線中斷等異常 |

### 6.2 定時報告範例

```
📊 每日報告 2026-01-19

帳戶總值: 52,350 USDT
總保證金率: 8.3%
持倉數量: 23 個

今日調整: 47 次
今日減倉: 0 次

⚠️ 低保證金倉位:
  • DOGE: 2.1%
  • SHIB: 2.4%
```

### 6.3 資料庫結構

```sql
-- 保證金調整記錄
CREATE TABLE margin_adjustments (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME,
    symbol TEXT,
    direction TEXT,        -- 'increase' / 'decrease'
    amount DECIMAL,
    before_margin DECIMAL,
    after_margin DECIMAL,
    trigger_type TEXT      -- 'scheduled' / 'emergency'
);

-- 減倉記錄
CREATE TABLE liquidations (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME,
    symbol TEXT,
    side TEXT,             -- 'long' / 'short'
    quantity DECIMAL,
    price DECIMAL,
    released_margin DECIMAL,
    reason TEXT
);

-- 帳戶快照（每小時）
CREATE TABLE account_snapshots (
    id INTEGER PRIMARY KEY,
    timestamp DATETIME,
    total_equity DECIMAL,
    total_margin DECIMAL,
    available_balance DECIMAL,
    positions_json TEXT    -- 所有倉位的 JSON 快照
);
```

---

## 7. 配置檔結構

```yaml
# config.yaml

# Bitfinex API
bitfinex:
  api_key: ${BITFINEX_API_KEY}      # 從環境變數讀取
  api_secret: ${BITFINEX_API_SECRET}

# Telegram 通知
telegram:
  bot_token: ${TELEGRAM_BOT_TOKEN}
  chat_id: ${TELEGRAM_CHAT_ID}

# 監控設定
monitor:
  poll_interval_sec: 60             # 定時輪詢間隔
  volatility_update_hours: 1        # 波動率更新間隔
  volatility_lookback_days: 7       # 波動率計算天數

# 觸發閾值
thresholds:
  min_adjustment_usdt: 50           # 最小調整金額
  min_deviation_pct: 5              # 最小偏離百分比
  emergency_margin_rate: 2          # 緊急保證金率
  price_spike_pct: 3                # 價格劇烈波動閾值

# 風險權重（手動覆蓋）
risk_weights:
  BTC: 1.0
  ETH: 1.2
  # ... 其他幣種

# 倉位優先級（減倉用）
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
  dry_run: true                     # 上線前務必設 false
```

---

## 8. 專案結構

```
bitfinex-margin-balancer/
├── config/
│   ├── config.yaml              # 主配置檔
│   └── config.example.yaml      # 範例配置
├── src/
│   ├── __init__.py
│   ├── main.py                  # 程式入口
│   ├── config_manager.py        # 配置載入
│   ├── api/
│   │   ├── bitfinex_client.py   # Bitfinex REST API
│   │   └── bitfinex_ws.py       # Bitfinex WebSocket
│   ├── core/
│   │   ├── risk_calculator.py   # 風險/波動率計算
│   │   ├── margin_allocator.py  # 保證金分配邏輯
│   │   └── position_liquidator.py # 減倉執行器
│   ├── scheduler/
│   │   ├── poll_scheduler.py    # 定時輪詢
│   │   └── event_detector.py    # 緊急事件偵測
│   ├── notifier/
│   │   └── telegram_bot.py      # Telegram 通知
│   └── storage/
│       ├── database.py          # SQLite 操作
│       └── models.py            # 資料模型
├── tests/                       # 單元測試
├── logs/                        # 日誌檔
├── requirements.txt
└── README.md
```

---

## 9. 錯誤處理

| 錯誤類型 | 處理方式 |
|---------|---------|
| 網路逾時 | 重試 3 次，間隔指數遞增 (1s,2s,4s) |
| API Rate Limit | 暫停 60 秒，降低輪詢頻率 |
| 認證失敗 | 停止服務，發送緊急通知 |
| 餘額不足 | 觸發減倉流程 |
| 訂單失敗 | 記錄錯誤，通知後跳過該倉位 |
| WebSocket 斷線 | 自動重連，重連失敗切換純輪詢模式 |

---

## 10. 安全機制

### 10.1 API 金鑰權限
- 只需「讀取」+「交易」權限
- 不需要「提款」權限

### 10.2 運行保護
- `dry_run` 模式：測試時只通知不執行
- 單次調整上限：防止異常大額操作
- 冷卻時間：避免連續頻繁操作

### 10.3 減倉保護
- `require_confirmation`：可選人工確認
- `max_single_close_pct`：單次最多平 25%
- 總開關 `enabled`：可快速停用

### 10.4 監控保護
- 心跳檢測：每 5 分鐘發送心跳到 Telegram
- 異常靜默警告：超過 10 分鐘無動作則告警

### 10.5 資料保護
- API 金鑰從環境變數讀取，不寫入配置檔
- SQLite 資料庫定期備份

---

## 11. 啟動前檢查

程式啟動時自動檢查：
- ✓ API 金鑰有效性
- ✓ API 權限是否足夠（不含提款權限）
- ✓ Telegram Bot 連線
- ✓ 資料庫可寫入
- ✓ 配置檔格式正確
- ✓ 至少有一個有效倉位

任一項失敗 → 停止啟動，發送錯誤通知

---

## 附錄：Bitfinex 衍生品規則參考

- 帳戶要求：中級（Intermediate）或更高驗證等級
- 資金：需將 BTC 或 USDt 轉入衍生品錢包
- 最高槓桿：100 倍（視交易對而定）
- 維持保證金率：0.5%
- 抵押品最低要求：開倉名義價值的 1%
