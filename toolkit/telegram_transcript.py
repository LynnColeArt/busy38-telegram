#!/usr/bin/env python3
# SPDX-License-Identifier: GPL-3.0-only
"""
Telegram transcript logger for Busy38.

Design goal matches busy-38-discord:
- Persist observed messages into DuckDB (chat_entries).
- Provide broad pattern search with snippets (recency-biased by default).
- Provide "around" context drill-down by fully-qualified message id.

Important Telegram detail:
Message IDs are only unique per chat, so we store ids as:
  telegram:<chat_id>:<message_id>
and store project_id as:
  telegram:<chat_id>
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, List

try:
    import duckdb
except Exception:  # pragma: no cover
    duckdb = None

logger = logging.getLogger(__name__)


class TelegramTranscriptLogger:
    def __init__(self, data_dir: str = "./data/memory"):
        self.data_dir = Path(data_dir)
        self._conn = None

    def connect(self) -> None:
        if duckdb is None:
            raise RuntimeError("duckdb not installed")
        self.data_dir.mkdir(parents=True, exist_ok=True)
        path = self.data_dir / "chat_logs.duckdb"
        self._conn = duckdb.connect(str(path))
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS chat_entries (
                id VARCHAR PRIMARY KEY, timestamp TIMESTAMP, content TEXT,
                vector TEXT, project_id VARCHAR, participants TEXT, topics TEXT,
                metadata TEXT, expires_at TIMESTAMP, created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            """
        )
        self._conn.execute("CREATE INDEX IF NOT EXISTS chat_timestamp_idx ON chat_entries(timestamp)")
        self._conn.execute("CREATE INDEX IF NOT EXISTS chat_project_idx ON chat_entries(project_id)")

    def close(self) -> None:
        if self._conn is not None:
            try:
                self._conn.close()
            except Exception:
                pass
        self._conn = None

    def _ensure(self) -> None:
        if self._conn is None:
            self.connect()

    def log_message(
        self,
        *,
        chat_id: str,
        message_id: str,
        timestamp: Optional[datetime],
        content: str,
        metadata: Dict[str, Any],
        participants: Optional[list[int]] = None,
        topics: Optional[list[str]] = None,
        expires_at: Optional[datetime] = None,
    ) -> str:
        """
        Insert one observed message idempotently.

        Returns the stored chat_entries.id.
        """
        self._ensure()

        ts = timestamp or datetime.now(timezone.utc)
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)

        cid = str(chat_id)
        mid = str(message_id)
        source_id = f"telegram:{cid}:{mid}"
        project_id = f"telegram:{cid}"

        try:
            self._conn.execute(
                """
                INSERT INTO chat_entries
                  (id, timestamp, content, vector, project_id, participants, topics, metadata, expires_at)
                SELECT ?, ?, ?, ?, ?, ?, ?, ?, ?
                WHERE NOT EXISTS (SELECT 1 FROM chat_entries WHERE id = ?)
                """,
                [
                    source_id,
                    ts.isoformat(),
                    str(content or ""),
                    "[]",
                    project_id,
                    json.dumps(participants or []),
                    json.dumps(topics or []),
                    json.dumps(metadata or {}, separators=(",", ":"), sort_keys=True),
                    expires_at.isoformat() if expires_at else None,
                    source_id,
                ],
            )
        except Exception as exc:
            logger.debug("Failed to log telegram message %s: %s", source_id, exc)
        return source_id

    def search(
        self,
        *,
        query: str,
        project_id: Optional[str] = None,
        project_id_prefix: str = "telegram:",
        since: Optional[datetime] = None,
        max_messages: int = 2000,
        context: int = 80,
        case_sensitive: bool = True,
        regex: bool = False,
        snippets_per_message: int = 3,
        max_results: int = 50,
    ) -> List[Dict[str, Any]]:
        """
        Pattern search across chat_entries, matching Busy38's dlog UX.
        """
        self._ensure()
        if not query:
            return []

        flags = 0 if case_sensitive else re.IGNORECASE
        try:
            pattern = re.compile(query if regex else re.escape(query), flags)
        except re.error as exc:
            raise ValueError(f"invalid regex: {exc}")

        where = "project_id = ?" if project_id else "project_id LIKE ?"
        param = project_id if project_id else f"{project_id_prefix}%"

        clauses = [where]
        params: list[Any] = [param]
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())
        params.append(int(max_messages))
        where_sql = " AND ".join(clauses)

        rows = self._conn.execute(
            f"""
            SELECT id, timestamp, content, project_id, metadata
            FROM chat_entries
            WHERE {where_sql}
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            params,
        ).fetchall()

        results: List[Dict[str, Any]] = []
        for rid, ts, content, pid, meta in rows:
            if not content:
                continue
            matches = list(pattern.finditer(content))
            if not matches:
                continue

            snippets: List[str] = []
            for m in matches[:snippets_per_message]:
                start = max(0, m.start() - int(context))
                end = min(len(content), m.end() + int(context))
                snippets.append(content[start:end])

            try:
                meta_obj = json.loads(meta) if isinstance(meta, str) and meta else {}
            except Exception:
                meta_obj = {}

            results.append(
                {
                    "id": rid,
                    "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    "project_id": pid,
                    "snippets": snippets,
                    "metadata": meta_obj,
                }
            )
            if len(results) >= int(max_results):
                break

        return results

    def context_around(self, *, source_id: str, before: int = 8, after: int = 8) -> List[Dict[str, Any]]:
        """
        Return message context around a specific chat_entries.id (telegram:<chat_id>:<message_id>).
        """
        self._ensure()

        row = self._conn.execute(
            "SELECT id, timestamp, content, project_id, metadata FROM chat_entries WHERE id = ?",
            [str(source_id)],
        ).fetchone()
        if not row:
            return []

        rid, ts, content, pid, meta = row

        before_rows = self._conn.execute(
            """
            SELECT id, timestamp, content, project_id, metadata
            FROM chat_entries
            WHERE project_id = ? AND timestamp < ?
            ORDER BY timestamp DESC
            LIMIT ?
            """,
            [pid, ts, int(before)],
        ).fetchall()
        after_rows = self._conn.execute(
            """
            SELECT id, timestamp, content, project_id, metadata
            FROM chat_entries
            WHERE project_id = ? AND timestamp > ?
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            [pid, ts, int(after)],
        ).fetchall()

        combined = list(reversed(before_rows)) + [row] + list(after_rows)
        out: List[Dict[str, Any]] = []
        for rid2, ts2, content2, pid2, meta2 in combined:
            try:
                meta_obj = json.loads(meta2) if isinstance(meta2, str) and meta2 else {}
            except Exception:
                meta_obj = {}
            out.append(
                {
                    "id": rid2,
                    "timestamp": ts2.isoformat() if hasattr(ts2, "isoformat") else str(ts2),
                    "project_id": pid2,
                    "content": content2 or "",
                    "metadata": meta_obj,
                }
            )
        return out

    def recent_messages(
        self,
        *,
        project_id: str,
        since: Optional[datetime] = None,
        max_age_hours: int = 72,
        now: Optional[datetime] = None,
        limit: int = 800,
    ) -> List[Dict[str, Any]]:
        """
        Fetch recent messages for one telegram chat (project_id) in chronological order.
        """
        self._ensure()

        if since is None and int(max_age_hours) > 0:
            ref = now or datetime.now(timezone.utc)
            since = ref - timedelta(hours=int(max_age_hours))

        clauses = ["project_id = ?"]
        params: List[Any] = [str(project_id)]
        if since is not None:
            if since.tzinfo is None:
                since = since.replace(tzinfo=timezone.utc)
            clauses.append("timestamp >= ?")
            params.append(since.isoformat())
        params.append(int(limit))

        rows = self._conn.execute(
            f"""
            SELECT id, timestamp, content, metadata
            FROM chat_entries
            WHERE {' AND '.join(clauses)}
            ORDER BY timestamp ASC
            LIMIT ?
            """,
            params,
        ).fetchall()

        out: List[Dict[str, Any]] = []
        for rid, ts, content, meta in rows:
            try:
                meta_obj = json.loads(meta) if isinstance(meta, str) and meta else {}
            except Exception:
                meta_obj = {}
            out.append(
                {
                    "id": rid,
                    "timestamp": ts.isoformat() if hasattr(ts, "isoformat") else str(ts),
                    "content": content or "",
                    "metadata": meta_obj,
                }
            )
        return out

