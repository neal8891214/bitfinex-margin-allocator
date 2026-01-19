"""Database 模組測試"""

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
async def db(tmp_path: Path) -> Database:
    """建立測試用資料庫"""
    db_path = tmp_path / "test.db"
    database = Database(str(db_path))
    await database.initialize()
    yield database
    await database.close()


@pytest.mark.asyncio
async def test_database_initialize(db: Database) -> None:
    """測試資料庫初始化"""
    # 檢查表是否存在
    tables = await db.get_tables()
    assert "margin_adjustments" in tables
    assert "liquidations" in tables
    assert "account_snapshots" in tables


@pytest.mark.asyncio
async def test_save_and_get_margin_adjustment(db: Database) -> None:
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
async def test_get_margin_adjustments_with_symbol_filter(db: Database) -> None:
    """測試使用 symbol 過濾取得保證金調整記錄"""
    # 新增 BTC 調整記錄
    adj_btc = MarginAdjustment(
        timestamp=datetime(2026, 1, 19, 12, 0, 0),
        symbol="BTC",
        direction=AdjustmentDirection.INCREASE,
        amount=Decimal("100"),
        before_margin=Decimal("400"),
        after_margin=Decimal("500"),
        trigger_type=TriggerType.SCHEDULED,
    )
    await db.save_margin_adjustment(adj_btc)

    # 新增 ETH 調整記錄
    adj_eth = MarginAdjustment(
        timestamp=datetime(2026, 1, 19, 12, 1, 0),
        symbol="ETH",
        direction=AdjustmentDirection.DECREASE,
        amount=Decimal("50"),
        before_margin=Decimal("300"),
        after_margin=Decimal("250"),
        trigger_type=TriggerType.EMERGENCY,
    )
    await db.save_margin_adjustment(adj_eth)

    # 過濾 BTC
    btc_records = await db.get_margin_adjustments(limit=10, symbol="BTC")
    assert len(btc_records) == 1
    assert btc_records[0].symbol == "BTC"

    # 過濾 ETH
    eth_records = await db.get_margin_adjustments(limit=10, symbol="ETH")
    assert len(eth_records) == 1
    assert eth_records[0].symbol == "ETH"

    # 無過濾
    all_records = await db.get_margin_adjustments(limit=10)
    assert len(all_records) == 2


@pytest.mark.asyncio
async def test_save_and_get_liquidation(db: Database) -> None:
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
async def test_save_and_get_account_snapshot(db: Database) -> None:
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
async def test_get_daily_stats(db: Database) -> None:
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


@pytest.mark.asyncio
async def test_get_daily_stats_empty(db: Database) -> None:
    """測試取得空日期的統計"""
    stats = await db.get_daily_stats(datetime(2026, 1, 20).date())
    assert stats["adjustment_count"] == 0
    assert stats["liquidation_count"] == 0
