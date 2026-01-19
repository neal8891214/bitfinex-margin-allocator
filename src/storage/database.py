"""SQLite 資料庫操作模組"""

import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, Dict, List, Optional

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
        self._conn: Optional[aiosqlite.Connection] = None

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
        assert self._conn is not None
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

    async def get_tables(self) -> List[str]:
        """取得所有表名"""
        assert self._conn is not None
        cursor = await self._conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
        rows = await cursor.fetchall()
        return [row["name"] for row in rows]

    async def save_margin_adjustment(self, adj: MarginAdjustment) -> int:
        """儲存保證金調整記錄"""
        assert self._conn is not None
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
        return cursor.lastrowid or 0

    async def get_margin_adjustments(
        self, limit: int = 100, symbol: Optional[str] = None
    ) -> List[MarginAdjustment]:
        """取得保證金調整記錄"""
        assert self._conn is not None
        query = "SELECT * FROM margin_adjustments"
        params: List[Any] = []

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
        assert self._conn is not None
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
        return cursor.lastrowid or 0

    async def get_liquidations(self, limit: int = 100) -> List[Liquidation]:
        """取得減倉記錄"""
        assert self._conn is not None
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
        assert self._conn is not None
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
        return cursor.lastrowid or 0

    async def get_account_snapshots(self, limit: int = 100) -> List[AccountSnapshot]:
        """取得帳戶快照"""
        assert self._conn is not None
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

    async def get_daily_stats(self, target_date: date) -> Dict[str, int]:
        """取得指定日期的統計"""
        assert self._conn is not None
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
            "adjustment_count": adj_row["count"] if adj_row else 0,
            "liquidation_count": liq_row["count"] if liq_row else 0,
        }
