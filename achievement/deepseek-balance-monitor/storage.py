"""
SQLite 存储层 —— 余额历史记录
"""

import sqlite3
import os
from datetime import datetime
from typing import Optional


DB_FILENAME = "balance_history.db"


def get_db_path() -> str:
    """获取数据库文件路径（与脚本同目录）"""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base_dir, DB_FILENAME)


def init_db() -> None:
    """初始化数据库表"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS balance_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            timestamp TEXT NOT NULL,
            total_balance REAL NOT NULL,
            granted_balance REAL NOT NULL,
            topped_up_balance REAL NOT NULL,
            currency TEXT NOT NULL DEFAULT 'CNY',
            is_available INTEGER NOT NULL DEFAULT 1
        )
    """)
    cursor.execute("""
        CREATE INDEX IF NOT EXISTS idx_timestamp
        ON balance_history(timestamp DESC)
    """)
    conn.commit()
    conn.close()


def save_balance(total: float, granted: float, topped_up: float,
                 currency: str = "CNY", is_available: bool = True) -> None:
    """保存一条余额记录"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO balance_history "
        "(timestamp, total_balance, granted_balance, topped_up_balance, currency, is_available) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            datetime.now().isoformat(),
            total,
            granted,
            topped_up,
            currency,
            1 if is_available else 0,
        ),
    )
    conn.commit()
    conn.close()


def get_latest_balance() -> Optional[dict]:
    """获取最近一次余额记录"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM balance_history ORDER BY timestamp DESC LIMIT 1"
    )
    row = cursor.fetchone()
    conn.close()
    return dict(row) if row else None


def get_balance_history(limit: int = 50) -> list[dict]:
    """获取最近 N 条余额历史"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute(
        "SELECT * FROM balance_history ORDER BY timestamp DESC LIMIT ?",
        (limit,),
    )
    rows = cursor.fetchall()
    conn.close()
    return [dict(r) for r in reversed(rows)]


def get_daily_summary(days: int = 7) -> list[dict]:
    """获取最近 N 天的每日消耗汇总"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute("""
        SELECT
            date(timestamp) as day,
            MIN(total_balance) as min_balance,
            MAX(total_balance) as max_balance,
            ROUND(MAX(total_balance) - MIN(total_balance), 4) as daily_spend
        FROM balance_history
        WHERE timestamp >= date('now', ?)
        GROUP BY date(timestamp)
        ORDER BY day ASC
    """, (f"-{days} days",))
    rows = cursor.fetchall()
    conn.close()
    return [
        {"day": r[0], "min_balance": r[1], "max_balance": r[2], "daily_spend": r[3]}
        for r in rows
    ]


def cleanup_old_records(keep_days: int = 90) -> int:
    """清理超过 N 天的旧记录，返回删除行数"""
    db_path = get_db_path()
    conn = sqlite3.connect(db_path)
    cursor = conn.cursor()
    cursor.execute(
        "DELETE FROM balance_history WHERE timestamp < date('now', ?)",
        (f"-{keep_days} days",),
    )
    deleted = cursor.rowcount
    conn.commit()
    conn.close()
    return deleted
