# -*- coding: utf-8 -*-
"""
SAM Agent 轨迹持久化（SQLite）
记录每一步的工具调用、结果、核验状态，支持回溯审计
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional


class TrajectoryStore:
    """轨迹持久化存储"""

    _instance: Optional["TrajectoryStore"] = None
    _lock = threading.Lock()

    @classmethod
    def instance(cls, db_path: Optional[str] = None) -> "TrajectoryStore":
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls(db_path)
        return cls._instance

    @classmethod
    def reset(cls) -> None:
        with cls._lock:
            if cls._instance is not None:
                try:
                    cls._instance.close()
                except Exception:
                    pass
            cls._instance = None

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            db_path = str(Path.home() / ".sam_agent" / "trajectories.db")
        self._db_path = Path(db_path)
        self._db_path.parent.mkdir(parents=True, exist_ok=True)
        self._local = threading.local()
        self._init_db()

    def _get_conn(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            self._local.conn = sqlite3.connect(str(self._db_path))
            self._local.conn.row_factory = sqlite3.Row
            self._local.conn.execute("PRAGMA journal_mode=WAL")
            self._local.conn.execute("PRAGMA busy_timeout=5000")
        return self._local.conn

    def close(self) -> None:
        """关闭当前线程的数据库连接（Windows 必需，否则无法删除文件）"""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None

    def clear_task(self, task_id: str) -> int:
        """清空指定任务的所有轨迹（用于测试清理）"""
        conn = self._get_conn()
        result = conn.execute("DELETE FROM trajectories WHERE task_id = ?", (task_id,))
        conn.commit()
        return result.rowcount

    def clear_all(self) -> int:
        """清空全部轨迹（用于测试清理）"""
        conn = self._get_conn()
        result = conn.execute("DELETE FROM trajectories")
        conn.commit()
        return result.rowcount

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trajectories (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                step INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                action TEXT NOT NULL,
                tool_name TEXT,
                tool_input TEXT,
                tool_output TEXT,
                verdict TEXT,
                verdict_score REAL,
                metadata TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_task_id ON trajectories(task_id)
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_timestamp ON trajectories(timestamp)
        """)
        conn.commit()

    def record(
        self,
        task_id: str,
        step: int,
        action: str,
        tool_name: Optional[str] = None,
        tool_input: Optional[Dict[str, Any]] = None,
        tool_output: Optional[Any] = None,
        verdict: Optional[str] = None,
        verdict_score: Optional[float] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> int:
        conn = self._get_conn()
        row_id = conn.execute(
            """INSERT INTO trajectories
               (task_id, step, timestamp, action, tool_name, tool_input, tool_output, verdict, verdict_score, metadata)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                task_id,
                step,
                datetime.utcnow().isoformat(),
                action,
                tool_name,
                json.dumps(tool_input, ensure_ascii=False) if tool_input else None,
                json.dumps(tool_output, ensure_ascii=False) if tool_output is not None else None,
                verdict,
                verdict_score,
                json.dumps(metadata, ensure_ascii=False) if metadata else None,
            ),
        ).lastrowid
        conn.commit()
        return row_id

    def query_by_task(self, task_id: str) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM trajectories WHERE task_id = ? ORDER BY step", (task_id,)
        ).fetchall()
        return [dict(r) for r in rows]

    def query_recent(self, limit: int = 20) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM trajectories ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]

    def query_by_verdict(self, verdict: str, limit: int = 50) -> List[Dict[str, Any]]:
        conn = self._get_conn()
        rows = conn.execute(
            "SELECT * FROM trajectories WHERE verdict = ? ORDER BY id DESC LIMIT ?",
            (verdict, limit),
        ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> Dict[str, Any]:
        conn = self._get_conn()
        total = conn.execute("SELECT COUNT(*) FROM trajectories").fetchone()[0]
        by_verdict = {}
        for row in conn.execute(
            "SELECT verdict, COUNT(*) as cnt FROM trajectories GROUP BY verdict"
        ).fetchall():
            by_verdict[row["verdict"] or "None"] = row["cnt"]
        return {
            "db_path": str(self._db_path),
            "total_records": total,
            "by_verdict": by_verdict,
        }

    def purge_old(self, keep_days: int = 30) -> int:
        conn = self._get_conn()
        result = conn.execute(
            "DELETE FROM trajectories WHERE created_at < datetime('now', ?)",
            (f"-{keep_days} days",),
        )
        conn.commit()
        return result.rowcount

    def __repr__(self) -> str:
        return f"TrajectoryStore(db={self._db_path})"
