# 驗證清單 - Bitfinex Margin Allocator

## 🔴 關鍵驗證項目 (必須完成)

### API 驗證

#### 1. Bitfinex REST API 認證
- [ ] 在 Testnet 註冊帳戶: https://test.bitfinex.com/
- [ ] 取得 API Key 和 Secret (啟用衍生品交易權限)
- [ ] 驗證簽名生成算法:
  ```python
  # 參考官方文檔確認簽名格式
  message = f"/api{path}{nonce}{body}"
  # 確認是否需要其他欄位?
  ```
- [ ] 測試認證 API 呼叫:
  ```bash
  curl -X POST https://test.bitfinex.com/v2/auth/r/positions \
    -H "bfx-nonce: ..." \
    -H "bfx-apikey: ..." \
    -H "bfx-signature: ..."
  ```

#### 2. 倉位資料解析
- [ ] 呼叫 `/v2/auth/r/positions` 並記錄完整回應
- [ ] 驗證欄位索引 (特別是 raw[16], raw[17]):
  ```
  [16] PRICE (當前價格) - 確認
  [17] COLLATERAL (保證金) - 確認
  ```
- [ ] 測試多種幣種: BTC, ETH, XRP
- [ ] 測試 LONG 和 SHORT 倉位
- [ ] 驗證保證金率計算:
  ```
  margin_rate = (margin / (quantity * price)) * 100
  ```

#### 3. 保證金調整 API
- [ ] 確認 endpoint: `/v2/auth/w/deriv/collateral/set`
  - 可能是 `/v2/auth/w/position/collateral/set`?
  - 查閱官方文檔確認
- [ ] 驗證請求格式:
  ```json
  {
    "symbol": "tBTCF0:USTF0",
    "delta": "10.5"
  }
  ```
- [ ] 測試增加保證金 (delta > 0)
- [ ] 測試減少保證金 (delta < 0)
- [ ] 記錄成功回應格式:
  ```
  response[6] == "SUCCESS" ?
  ```

#### 4. 市價平倉 API
- [ ] 確認 endpoint: `/v2/auth/w/order/submit`
- [ ] 驗證請求格式:
  ```json
  {
    "type": "MARKET",
    "symbol": "tBTCF0:USTF0",
    "amount": "-0.1",  // LONG 平倉用負數
    "flags": 0  // 或需要 POS_CLOSE flag?
  }
  ```
- [ ] 測試 LONG 倉位平倉
- [ ] 測試 SHORT 倉位平倉
- [ ] 驗證訂單狀態

---

### 風險參數驗證

#### 5. 維持保證金率
- [ ] 查閱 Bitfinex 官方維持保證金要求
- [ ] 驗證代碼中的值:
  ```python
  MAINTENANCE_MARGIN_RATE = Decimal("0.005")  # 0.5%
  ```
- [ ] 確認不同槓桿的要求
- [ ] 測試爆倉閾值

#### 6. 保證金率計算
- [ ] 人工計算範例:
  ```
  倉位: 1 BTC @ $50,000
  保證金: $2,000
  保證金率 = $2,000 / (1 * $50,000) * 100 = 4%
  ```
- [ ] 與代碼計算結果比對
- [ ] 測試邊界情況 (margin=0, price=0)

#### 7. 緊急閾值設定
- [ ] 回測歷史資料驗證:
  - `emergency_margin_rate: 2.0` (2%)
  - `price_spike_pct: 3.0` (3%)
- [ ] 模擬極端市場情況
- [ ] 記錄誤觸發次數

---

### 測試流程

#### 8. Dry-run 模式測試
```bash
# 設定環境變數
export BITFINEX_API_KEY="testnet-key"
export BITFINEX_API_SECRET="testnet-secret"
export TELEGRAM_BOT_TOKEN="optional"

# 執行 dry-run
python -m src.main --config config/config.yaml --dry-run
```

檢查項目:
- [ ] 成功連線到 API
- [ ] 成功取得倉位資料
- [ ] 計算目標保證金
- [ ] 日誌顯示調整計畫 (但不執行)
- [ ] WebSocket 連線成功
- [ ] 收到價格更新

#### 9. 小額實際測試 (Testnet)
```bash
# 正常模式 (實際執行)
python -m src.main --config config/config.yaml
```

測試場景:
- [ ] 建立 1 個小額 BTC 倉位 ($100)
- [ ] 觀察自動重平衡
- [ ] 手動調整倉位保證金，觸發重平衡
- [ ] 降低保證金率，觸發緊急重平衡
- [ ] 測試 WebSocket 價格監控
- [ ] 測試減倉邏輯 (如果啟用)

#### 10. 錯誤情況測試
- [ ] 無效 API 金鑰
- [ ] 網路中斷
- [ ] WebSocket 斷線重連
- [ ] API rate limit
- [ ] 餘額不足
- [ ] 無效的保證金調整

---

## 🟡 配置和監控

### 配置驗證

#### 11. 環境變數
- [ ] 確認所有必要環境變數已設定:
  - `BITFINEX_API_KEY`
  - `BITFINEX_API_SECRET`
  - `TELEGRAM_BOT_TOKEN` (optional)
  - `TELEGRAM_CHAT_ID` (optional)
- [ ] 測試缺少環境變數的情況
- [ ] 驗證配置載入錯誤訊息

#### 12. 配置檔案
- [ ] 複製並編輯 `config/config.yaml`
- [ ] 調整閾值參數:
  ```yaml
  thresholds:
    min_adjustment_usdt: 50      # 根據資金規模調整
    min_deviation_pct: 5         # 根據策略調整
    emergency_margin_rate: 2.0   # 根據風險承受度調整
    price_spike_pct: 3.0         # 根據幣種波動性調整
  ```
- [ ] 設定倉位優先級
- [ ] 設定風險權重 (或使用自動計算)

#### 13. Telegram 通知
- [ ] 建立 Telegram Bot
- [ ] 取得 Chat ID
- [ ] 測試所有通知類型:
  - [ ] 啟動通知
  - [ ] 保證金調整報告
  - [ ] 減倉警報
  - [ ] API 錯誤警報
  - [ ] 帳戶保證金率警告

---

### 監控設定

#### 14. 日誌監控
- [ ] 確認日誌檔案位置: `logs/margin_balancer.log`
- [ ] 設定日誌輪轉 (避免檔案過大)
- [ ] 監控關鍵訊息:
  ```
  grep "ERROR" logs/margin_balancer.log
  grep "WARNING" logs/margin_balancer.log
  grep "Emergency" logs/margin_balancer.log
  ```

#### 15. 資料庫檢查
- [ ] 確認資料庫位置: `data/margin_balancer.db`
- [ ] 查詢歷史記錄:
  ```sql
  SELECT * FROM margin_adjustments ORDER BY timestamp DESC LIMIT 10;
  SELECT * FROM liquidations ORDER BY timestamp DESC LIMIT 10;
  SELECT * FROM account_snapshots ORDER BY timestamp DESC LIMIT 10;
  ```
- [ ] 設定定期清理 (避免資料庫過大)

#### 16. 健康檢查
- [ ] 定期檢查程序狀態:
  ```bash
  ps aux | grep "python -m src.main"
  ```
- [ ] 監控資源使用:
  ```bash
  # CPU, Memory
  top -p <pid>
  ```
- [ ] 設定程序守護 (systemd/supervisor)

---

## 🟢 生產環境準備

### 部署前檢查

#### 17. 安全檢查
- [ ] 不要將 API 金鑰提交到版本控制
- [ ] 確認 `.gitignore` 包含:
  ```
  config/config.yaml
  *.db
  logs/
  .env
  ```
- [ ] 使用環境變數或密鑰管理服務
- [ ] 限制 API 金鑰權限 (只啟用必要權限)

#### 18. 備份和恢復
- [ ] 定期備份資料庫:
  ```bash
  cp data/margin_balancer.db data/margin_balancer.db.$(date +%Y%m%d)
  ```
- [ ] 測試資料恢復流程
- [ ] 記錄配置歷史

#### 19. 優雅關閉
- [ ] 測試 Ctrl+C 關閉
- [ ] 測試 SIGTERM 訊號
- [ ] 確認所有連線正確關閉
- [ ] 驗證未完成的操作處理

---

### 逐步上線計畫

#### 階段 1: Testnet 測試 (1-2 天)
- [ ] 完成所有 API 驗證
- [ ] 執行 24 小時持續測試
- [ ] 模擬各種市場情況
- [ ] 檢視所有日誌和記錄

#### 階段 2: 最小資金測試 (1-2 週)
- [ ] 使用最小允許金額 ($100-$200)
- [ ] 只開啟 1-2 個倉位
- [ ] 保持 dry_run: true (減倉)
- [ ] 每日檢視日誌和結果

#### 階段 3: 小規模運行 (1 個月)
- [ ] 增加到 $1,000-$2,000
- [ ] 開啟 3-5 個倉位
- [ ] 啟用自動減倉 (dry_run: false)
- [ ] 持續監控和優化參數

#### 階段 4: 正常規模 (ongoing)
- [ ] 根據風險承受度增加資金
- [ ] 擴展到更多幣種
- [ ] 微調參數和策略
- [ ] 定期檢視和改進

---

## 📋 問題追蹤

### 發現的問題
日期 | 問題描述 | 嚴重程度 | 狀態
-----|---------|---------|-----
     |         |         |

### 待辦事項
- [ ] 
- [ ] 
- [ ] 

---

## 📞 緊急聯絡

### 出現問題時
1. **立即停止服務**:
   ```bash
   # 找到程序 PID
   ps aux | grep "python -m src.main"
   
   # 優雅關閉
   kill -TERM <pid>
   
   # 強制關閉 (如果需要)
   kill -9 <pid>
   ```

2. **檢查日誌**:
   ```bash
   tail -n 100 logs/margin_balancer.log
   ```

3. **備份資料**:
   ```bash
   cp data/margin_balancer.db data/margin_balancer.db.emergency
   ```

4. **檢查倉位狀態** (直接用 Bitfinex 網頁/App)

---

**最後更新**: 2026-01-20  
**下次檢視**: 完成 Testnet 測試後
