"""SQLite 持久化层。

为什么是 SQLite 而不是向量数据库：
个人/单机场景下调研产出的数据量在万级以内，SQLite 零运维、单文件可迁移，
向量直接以 JSON 存字段、内存里算余弦足够快。等数据量真的上来了，
只要替换这一层的实现（接口不变）就能换成 pgvector / Qdrant。
"""

from __future__ import annotations

import json
import sqlite3
import threading
import time
import uuid
from pathlib import Path
from typing import Any

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
    id          TEXT PRIMARY KEY,
    title       TEXT NOT NULL DEFAULT '',
    created_at  REAL NOT NULL,
    updated_at  REAL NOT NULL,
    meta        TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    seq         INTEGER NOT NULL,
    payload     TEXT NOT NULL,
    created_at  REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_messages_session ON messages(session_id, seq);

CREATE TABLE IF NOT EXISTS memories (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    content     TEXT NOT NULL UNIQUE,
    tags        TEXT NOT NULL DEFAULT '',
    embedding   TEXT,
    session_id  TEXT,
    created_at  REAL NOT NULL,
    hits        INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sources (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id  TEXT NOT NULL,
    label       TEXT NOT NULL,
    url         TEXT NOT NULL,
    title       TEXT NOT NULL DEFAULT '',
    fetched_at  REAL NOT NULL,
    UNIQUE(session_id, url)
);

CREATE TABLE IF NOT EXISTS evidence (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id   TEXT NOT NULL,
    source_id    INTEGER NOT NULL,
    chunk_index  INTEGER NOT NULL,
    content      TEXT NOT NULL,
    embedding    TEXT,
    created_at   REAL NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_evidence_session ON evidence(session_id);
"""


class Store:
    """所有持久化操作的唯一入口。

    子 Agent 会在线程里并发写，因此连接开 ``check_same_thread=False``
    并用一把互斥锁串行化写操作——SQLite 本身也不支持多写并发。
    """

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._lock = threading.RLock()
        with self._lock:
            self._conn.executescript(SCHEMA)
            self._conn.commit()

    def close(self) -> None:
        with self._lock:
            self._conn.close()

    def _execute(self, sql: str, params: tuple = ()) -> sqlite3.Cursor:
        with self._lock:
            cursor = self._conn.execute(sql, params)
            self._conn.commit()
            return cursor

    def _query(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._conn.execute(sql, params).fetchall()

    # -------------------------------------------------------------- 会话
    def create_session(self, title: str = "", meta: dict[str, Any] | None = None) -> str:
        session_id = uuid.uuid4().hex[:12]
        now = time.time()
        self._execute(
            "INSERT INTO sessions (id, title, created_at, updated_at, meta) VALUES (?,?,?,?,?)",
            (session_id, title, now, now, json.dumps(meta or {}, ensure_ascii=False)),
        )
        return session_id

    def touch_session(self, session_id: str, title: str | None = None) -> None:
        if title is None:
            self._execute("UPDATE sessions SET updated_at=? WHERE id=?", (time.time(), session_id))
        else:
            self._execute(
                "UPDATE sessions SET updated_at=?, title=? WHERE id=?",
                (time.time(), title, session_id),
            )

    def get_session(self, session_id: str) -> dict[str, Any] | None:
        rows = self._query(
            """SELECT s.id, s.title, s.created_at, s.updated_at, s.meta,
                      (SELECT COUNT(*) FROM messages m WHERE m.session_id=s.id) AS message_count
               FROM sessions s WHERE s.id=?""",
            (session_id,),
        )
        if not rows:
            return None
        result = dict(rows[0])
        result["meta"] = json.loads(result["meta"] or "{}")
        return result

    def update_session_meta(self, session_id: str, meta: dict[str, Any]) -> None:
        self._execute(
            "UPDATE sessions SET meta=?, updated_at=? WHERE id=?",
            (json.dumps(meta, ensure_ascii=False), time.time(), session_id),
        )

    def list_sessions(self, limit: int = 20) -> list[dict[str, Any]]:
        rows = self._query(
            """SELECT s.id, s.title, s.created_at, s.updated_at,
                      (SELECT COUNT(*) FROM messages m WHERE m.session_id = s.id) AS message_count
               FROM sessions s ORDER BY s.updated_at DESC LIMIT ?""",
            (limit,),
        )
        return [dict(r) for r in rows]

    # -------------------------------------------------------------- 消息
    def append_messages(self, session_id: str, payloads: list[dict[str, Any]]) -> None:
        if not payloads:
            return
        with self._lock:
            row = self._conn.execute(
                "SELECT COALESCE(MAX(seq), 0) AS s FROM messages WHERE session_id=?",
                (session_id,),
            ).fetchone()
            seq = row["s"]
            now = time.time()
            self._conn.executemany(
                "INSERT INTO messages (session_id, seq, payload, created_at) VALUES (?,?,?,?)",
                [
                    (session_id, seq + i + 1, json.dumps(p, ensure_ascii=False), now)
                    for i, p in enumerate(payloads)
                ],
            )
            self._conn.commit()

    def load_messages(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT payload FROM messages WHERE session_id=? ORDER BY seq", (session_id,)
        )
        return [json.loads(r["payload"]) for r in rows]

    # ---------------------------------------------------------- 长期记忆
    def upsert_memory(
        self,
        content: str,
        tags: str = "",
        embedding: list[float] | None = None,
        session_id: str | None = None,
    ) -> int:
        cursor = self._execute(
            """INSERT INTO memories (content, tags, embedding, session_id, created_at)
               VALUES (?,?,?,?,?)
               ON CONFLICT(content) DO UPDATE SET
                   tags=excluded.tags,
                   embedding=COALESCE(excluded.embedding, memories.embedding)""",
            (
                content,
                tags,
                json.dumps(embedding) if embedding else None,
                session_id,
                time.time(),
            ),
        )
        return int(cursor.lastrowid or 0)

    def all_memories(self) -> list[dict[str, Any]]:
        rows = self._query("SELECT id, content, tags, embedding, created_at, hits FROM memories")
        return [_row_with_vector(r) for r in rows]

    def bump_memory_hits(self, ids: list[int]) -> None:
        if not ids:
            return
        placeholders = ",".join("?" * len(ids))
        self._execute(f"UPDATE memories SET hits = hits + 1 WHERE id IN ({placeholders})", tuple(ids))

    def delete_memory(self, memory_id: int) -> bool:
        return self._execute("DELETE FROM memories WHERE id=?", (memory_id,)).rowcount > 0

    # -------------------------------------------------------- 来源与证据
    def add_source(self, session_id: str, url: str, title: str) -> tuple[int, str, bool]:
        """登记一个来源，返回 (source_id, 引用标签, 是否新建)。

        查重、编号、插入在同一把锁内完成，避免并行 fetch_url / 子 Agent
        并发 ingest 时出现重复 S 标签或同 URL 双写。
        """
        with self._lock:
            existing = self._conn.execute(
                "SELECT id, label FROM sources WHERE session_id=? AND url=?",
                (session_id, url),
            ).fetchall()
            if existing:
                return int(existing[0]["id"]), existing[0]["label"], False

            count = self._conn.execute(
                "SELECT COUNT(*) AS c FROM sources WHERE session_id=?",
                (session_id,),
            ).fetchone()["c"]
            label = f"S{count + 1}"
            try:
                cursor = self._conn.execute(
                    "INSERT INTO sources (session_id, label, url, title, fetched_at) VALUES (?,?,?,?,?)",
                    (session_id, label, url, title, time.time()),
                )
                self._conn.commit()
                return int(cursor.lastrowid or 0), label, True
            except sqlite3.IntegrityError:
                self._conn.rollback()
                row = self._conn.execute(
                    "SELECT id, label FROM sources WHERE session_id=? AND url=?",
                    (session_id, url),
                ).fetchone()
                if row is None:
                    raise
                return int(row["id"]), row["label"], False

    def list_sources(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT id, label, url, title, fetched_at FROM sources WHERE session_id=? ORDER BY id",
            (session_id,),
        )
        return [dict(r) for r in rows]

    def add_evidence(
        self,
        session_id: str,
        source_id: int,
        chunks: list[str],
        embeddings: list[list[float]] | None = None,
    ) -> int:
        now = time.time()
        rows = [
            (
                session_id,
                source_id,
                index,
                chunk,
                json.dumps(embeddings[index]) if embeddings and index < len(embeddings) else None,
                now,
            )
            for index, chunk in enumerate(chunks)
        ]
        with self._lock:
            self._conn.executemany(
                """INSERT INTO evidence
                   (session_id, source_id, chunk_index, content, embedding, created_at)
                   VALUES (?,?,?,?,?,?)""",
                rows,
            )
            self._conn.commit()
        return len(rows)

    def all_evidence(self, session_id: str) -> list[dict[str, Any]]:
        rows = self._query(
            """SELECT e.id, e.content, e.embedding, e.chunk_index,
                      s.label, s.url, s.title
               FROM evidence e JOIN sources s ON s.id = e.source_id
               WHERE e.session_id = ?""",
            (session_id,),
        )
        return [_row_with_vector(r) for r in rows]

    def count_evidence(self, session_id: str) -> int:
        return int(
            self._query("SELECT COUNT(*) AS c FROM evidence WHERE session_id=?", (session_id,))[0][
                "c"
            ]
        )


def _row_with_vector(row: sqlite3.Row) -> dict[str, Any]:
    data = dict(row)
    raw = data.pop("embedding", None)
    data["vector"] = json.loads(raw) if raw else []
    return data
