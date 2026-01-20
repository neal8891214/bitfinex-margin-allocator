# API 驗證指南

本指南協助你驗證 Bitfinex API 串接的正確性。

## 快速開始

### 1. 安裝依賴
```bash
pip install -e ".[dev]"
```

### 2. 設定環境變數

#### 使用 Testnet (建議)
```bash
export BITFINEX_API_KEY="your-testnet-api-key"
export BITFINEX_API_SECRET="your-testnet-api-secret"
export USE_TESTNET="true"
```

註冊 Testnet: https://test.bitfinex.com/

#### 使用正式環境 (請謹慎)
```bash
export BITFINEX_API_KEY="your-production-api-key"
export BITFINEX_API_SECRET="your-production-api-secret"
```

### 3. 執行 API 驗證腳本
```bash
python scripts/verify_api.py
```

## 驗證步驟

### 第一步: 基本 API 驗證

執行 `verify_api.py` 會測試:
1. ✅ 簽名生成
2. ✅ 取得錢包資訊
3. ✅ 取得倉位資訊
4. ✅ 取得帳戶資訊

**預期結果**: 所有測試通過 (4/4)

**如果失敗**, 檢查:
- API Key 和 Secret 是否正確
- API 權限是否包含衍生品交易
- 網路連線是否正常
- 簽名格式是否正確

### 第二步: 驗證倉位資料格式

在 Testnet 建立一個小額測試倉位，然後:

```python
# 執行此腳本查看原始回應
python -c "
import asyncio
from src.api.bitfinex_client import BitfinexClient
import os

async def check():
    client = BitfinexClient(
        os.environ['BITFINEX_API_KEY'],
        os.environ['BITFINEX_API_SECRET'],
        'https://test.bitfinex.com'
    )
    response = await client._request('POST', '/v2/auth/r/positions')
    print('Raw response:', response)
    
    if response:
        print('\\nParsing position...')
        pos = client._parse_position(response[0])
        print(f'Symbol: {pos.symbol}')
        print(f'Side: {pos.side}')
        print(f'Quantity: {pos.quantity}')
        print(f'Margin: {pos.margin}')
        print(f'Margin Rate: {pos.margin_rate}%')
    
    await client.close()

asyncio.run(check())
"
```

**驗證項目**:
- [ ] Symbol 解析正確 (如 "tBTCF0:USTF0" -> "BTC")
- [ ] 保證金 (margin) 值正確
- [ ] 保證金率計算正確
- [ ] LONG/SHORT 方向正確

### 第三步: 測試保證金調整 (小額)

**⚠️ 警告**: 此步驟會實際修改倉位保證金

```python
# 增加 1 USDT 保證金
python -c "
import asyncio
from src.api.bitfinex_client import BitfinexClient
from decimal import Decimal
import os

async def test():
    client = BitfinexClient(
        os.environ['BITFINEX_API_KEY'],
        os.environ['BITFINEX_API_SECRET'],
        'https://test.bitfinex.com'
    )
    
    # 先查看當前倉位
    positions = await client.get_positions()
    if not positions:
        print('No positions found')
        await client.close()
        return
    
    pos = positions[0]
    print(f'Current margin: {pos.margin}')
    
    # 測試增加 1 USDT
    symbol = client.get_full_symbol(pos.symbol)
    print(f'Updating margin for {symbol}...')
    
    success = await client.update_position_margin(symbol, Decimal('1'))
    print(f'Result: {success}')
    
    # 再次查看
    positions = await client.get_positions()
    new_margin = positions[0].margin
    print(f'New margin: {new_margin}')
    print(f'Change: {new_margin - pos.margin}')
    
    await client.close()

asyncio.run(test())
"
```

**驗證項目**:
- [ ] API 呼叫成功 (return True)
- [ ] 保證金實際增加了 1 USDT
- [ ] 檢查 Bitfinex 網頁確認變更

### 第四步: 測試市價平倉 (極小額)

**⚠️ 警告**: 此步驟會實際平倉

```python
# 平掉 0.001 BTC (約 $50-100)
python -c "
import asyncio
from src.api.bitfinex_client import BitfinexClient
from src.storage.models import PositionSide
from decimal import Decimal
import os

async def test():
    client = BitfinexClient(
        os.environ['BITFINEX_API_KEY'],
        os.environ['BITFINEX_API_SECRET'],
        'https://test.bitfinex.com'
    )
    
    positions = await client.get_positions()
    if not positions:
        print('No positions found')
        await client.close()
        return
    
    pos = positions[0]
    print(f'Position: {pos.symbol} {pos.side} {pos.quantity}')
    
    # 平掉 0.001 (或更小)
    close_qty = min(pos.quantity, Decimal('0.001'))
    print(f'Closing {close_qty}...')
    
    symbol = client.get_full_symbol(pos.symbol)
    success = await client.close_position(symbol, pos.side, close_qty)
    print(f'Result: {success}')
    
    # 再次查看
    positions = await client.get_positions()
    if positions and positions[0].symbol == pos.symbol:
        new_qty = positions[0].quantity
        print(f'New quantity: {new_qty}')
        print(f'Closed: {pos.quantity - new_qty}')
    
    await client.close()

asyncio.run(test())
"
```

**驗證項目**:
- [ ] API 呼叫成功
- [ ] 倉位數量減少
- [ ] 檢查 Bitfinex 網頁確認訂單執行

### 第五步: 完整 Dry-run 測試

```bash
# 確保配置中 dry_run 為 true
python -m src.main --config config/config.yaml --dry-run
```

觀察 24 小時，檢查:
- [ ] 無錯誤日誌
- [ ] 正確計算目標保證金
- [ ] WebSocket 連線穩定
- [ ] 價格更新正常
- [ ] 緊急情況偵測正常 (如果觸發)

### 第六步: 小額實際測試

**⚠️ 確認前面所有步驟都成功後再執行**

1. 關閉 dry-run:
   ```yaml
   # config/config.yaml
   liquidation:
     dry_run: false  # ⚠️ 啟用實際減倉
   ```

2. 使用最小資金 (建議 $100-$200)

3. 執行服務:
   ```bash
   python -m src.main --config config/config.yaml
   ```

4. 監控日誌:
   ```bash
   tail -f logs/margin_balancer.log
   ```

5. 持續觀察 1-2 週

## 常見問題

### Q: 簽名錯誤 (401 Unauthorized)
A: 
1. 確認 API Key 和 Secret 正確
2. 確認簽名格式: `/api{path}{nonce}{body}`
3. 確認 nonce 是遞增的微秒時間戳
4. 參考官方文檔: https://docs.bitfinex.com/reference/rest-auth

### Q: 倉位資料解析錯誤
A:
1. 記錄完整的原始回應
2. 對照官方文檔確認欄位索引
3. 可能需要調整 `_parse_position()` 中的索引

### Q: 保證金調整失敗
A:
1. 確認 endpoint 正確
2. 確認參數格式
3. 檢查回應格式
4. 可能需要額外參數 (如 `type`)

### Q: WebSocket 連線不穩定
A:
1. 檢查網路連線
2. 增加重連延遲
3. 使用 REST API 作為 fallback

## 官方參考資料

- REST API 文檔: https://docs.bitfinex.com/reference/rest-public-candles
- WebSocket 文檔: https://docs.bitfinex.com/reference/ws-public-ticker
- Python SDK: https://github.com/bitfinexcom/bitfinex-api-py
- Testnet: https://test.bitfinex.com/

## 支援

如遇到問題:
1. 檢查 `CODE_REVIEW_REPORT.md` 中的已知問題
2. 查閱 `VERIFICATION_CHECKLIST.md`
3. 記錄詳細的錯誤訊息和日誌
4. 對照官方文檔驗證實作

---

**重要提醒**: 
- ✅ 先在 Testnet 完整測試
- ✅ 使用最小資金開始
- ✅ 保持 dry-run 直到完全確認
- ✅ 持續監控和記錄
