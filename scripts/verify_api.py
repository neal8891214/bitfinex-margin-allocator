#!/usr/bin/env python3
"""
Bitfinex API 驗證工具

用於驗證 API 連線、簽名和基本操作的獨立腳本。
使用前請設定環境變數: BITFINEX_API_KEY, BITFINEX_API_SECRET
"""

import asyncio
import hashlib
import hmac
import json
import os
import time
from decimal import Decimal
from typing import Any, Dict, Optional

import aiohttp


class BitfinexAPITester:
    """Bitfinex API 測試工具"""

    def __init__(
        self, 
        api_key: str, 
        api_secret: str, 
        base_url: str = "https://api.bitfinex.com"
    ):
        self.api_key = api_key
        self.api_secret = api_secret
        self.base_url = base_url.rstrip("/")
        self._session: Optional[aiohttp.ClientSession] = None

    async def __aenter__(self):
        self._session = aiohttp.ClientSession()
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        if self._session:
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
        self, 
        method: str, 
        path: str, 
        body: Optional[Dict[str, Any]] = None
    ) -> Any:
        """發送已認證請求"""
        if not self._session:
            raise RuntimeError("Session not initialized. Use 'async with' context.")

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

        print(f"\n{'='*60}")
        print(f"Request: {method} {path}")
        print(f"Nonce: {nonce}")
        print(f"Body: {body_json}")
        print(f"Signature: {signature[:20]}...")
        print(f"{'='*60}\n")

        async with self._session.request(
            method, url, headers=headers, data=body_json
        ) as response:
            status = response.status
            text = await response.text()
            
            print(f"Response Status: {status}")
            print(f"Response Body: {text[:500]}")
            
            if status != 200:
                print(f"\n❌ Error: HTTP {status}")
                return None
            
            return json.loads(text)

    async def test_get_wallets(self) -> bool:
        """測試取得錢包資訊"""
        print("\n🔍 Test 1: Get Wallets")
        try:
            response = await self._request("POST", "/v2/auth/r/wallets")
            if response:
                print(f"✅ Success: Retrieved {len(response)} wallets")
                for wallet in response:
                    wallet_type, currency, balance = wallet[0], wallet[1], wallet[2]
                    print(f"  - {wallet_type} {currency}: {balance}")
                return True
            return False
        except Exception as e:
            print(f"❌ Failed: {e}")
            return False

    async def test_get_positions(self) -> bool:
        """測試取得倉位資訊"""
        print("\n🔍 Test 2: Get Positions")
        try:
            response = await self._request("POST", "/v2/auth/r/positions")
            if response is not None:
                print(f"✅ Success: Retrieved {len(response)} positions")
                for pos in response:
                    if len(pos) > 17:
                        symbol = pos[0]
                        status = pos[1]
                        amount = pos[2]
                        margin = pos[17] if len(pos) > 17 else "N/A"
                        print(f"  - {symbol} [{status}]: Amount={amount}, Margin={margin}")
                return True
            return False
        except Exception as e:
            print(f"❌ Failed: {e}")
            return False

    async def test_get_account_info(self) -> bool:
        """測試取得帳戶資訊"""
        print("\n🔍 Test 3: Get Account Summary Info")
        try:
            response = await self._request("POST", "/v2/auth/r/info/user")
            if response is not None:
                print(f"✅ Success: Retrieved account info")
                print(f"Response: {json.dumps(response, indent=2)}")
                return True
            return False
        except Exception as e:
            print(f"❌ Failed: {e}")
            return False

    async def test_signature_generation(self) -> bool:
        """測試簽名生成 (不發送請求)"""
        print("\n🔍 Test 4: Signature Generation")
        path = "/v2/auth/r/wallets"
        nonce = "1234567890"
        body = "{}"
        
        signature = self._generate_signature(path, nonce, body)
        
        print(f"Path: {path}")
        print(f"Nonce: {nonce}")
        print(f"Body: {body}")
        print(f"Message: /api{path}{nonce}{body}")
        print(f"Signature: {signature}")
        
        print("\n✅ Signature generated successfully")
        print("⚠️  請對照 Bitfinex 官方範例驗證格式")
        return True


async def main():
    """主測試流程"""
    print("=" * 60)
    print("Bitfinex API 驗證工具")
    print("=" * 60)

    # 讀取環境變數
    api_key = os.environ.get("BITFINEX_API_KEY")
    api_secret = os.environ.get("BITFINEX_API_SECRET")
    
    # 可選: 使用 Testnet
    use_testnet = os.environ.get("USE_TESTNET", "false").lower() == "true"
    base_url = "https://test.bitfinex.com" if use_testnet else "https://api.bitfinex.com"

    if not api_key or not api_secret:
        print("\n❌ Error: 請設定環境變數")
        print("export BITFINEX_API_KEY='your-api-key'")
        print("export BITFINEX_API_SECRET='your-api-secret'")
        print("export USE_TESTNET='true'  # (可選) 使用 Testnet")
        return 1

    print(f"\n📍 環境: {'Testnet' if use_testnet else 'Production'}")
    print(f"📍 Base URL: {base_url}")
    print(f"📍 API Key: {api_key[:10]}...")

    async with BitfinexAPITester(api_key, api_secret, base_url) as tester:
        results = []
        
        # 執行測試
        results.append(await tester.test_signature_generation())
        results.append(await tester.test_get_wallets())
        results.append(await tester.test_get_positions())
        results.append(await tester.test_get_account_info())

        # 總結
        print("\n" + "=" * 60)
        print("測試總結")
        print("=" * 60)
        passed = sum(results)
        total = len(results)
        print(f"✅ 通過: {passed}/{total}")
        print(f"❌ 失敗: {total - passed}/{total}")

        if passed == total:
            print("\n🎉 所有測試通過!")
            print("\n下一步:")
            print("1. 驗證倉位資料格式是否與代碼解析一致")
            print("2. 測試保證金調整 API (小額)")
            print("3. 測試市價平倉 API (小額)")
            return 0
        else:
            print("\n⚠️  部分測試失敗，請檢查:")
            print("1. API Key 和 Secret 是否正確")
            print("2. API 權限是否足夠 (需要衍生品交易權限)")
            print("3. 網路連線是否正常")
            print("4. 簽名格式是否與官方文檔一致")
            return 1


if __name__ == "__main__":
    exit_code = asyncio.run(main())
    exit(exit_code)
