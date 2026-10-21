"""SQLite persistence for conversations and messages."""
from __future__ import annotations

import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from .settings_store import app_data_dir


class ConversationStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self.path = Path(path) if path else app_data_dir() / "conversations.sqlite3"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, timeout=10)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA foreign_keys=ON")
        return conn

    @contextmanager
    def _connection(self):
        conn = self._connect()
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def _initialize(self) -> None:
        with self._connection() as conn:
            conn.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    title TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    is_pinned INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    conversation_id INTEGER NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    model TEXT,
                    duration_ms INTEGER
                );
                CREATE INDEX IF NOT EXISTS idx_messages_conversation
                    ON messages(conversation_id, id);
                CREATE INDEX IF NOT EXISTS idx_conversations_updated
                    ON conversations(updated_at DESC);
            """)
            # Keep existing history when opening databases from older builds.
            columns = {row["name"] for row in conn.execute("PRAGMA table_info(conversations)")}
            if "is_pinned" not in columns:
                conn.execute("ALTER TABLE conversations ADD COLUMN is_pinned INTEGER NOT NULL DEFAULT 0")

    def create_conversation(self, title: str = "Новый диалог") -> int:
        now = time.time()
        with self._connection() as conn:
            cur = conn.execute(
                "INSERT INTO conversations(title, created_at, updated_at) VALUES (?, ?, ?)",
                (title.strip() or "Новый диалог", now, now),
            )
            return int(cur.lastrowid)

    def conversation_exists(self, conversation_id: int) -> bool:
        with self._connection() as conn:
            row = conn.execute("SELECT 1 FROM conversations WHERE id=?", (conversation_id,)).fetchone()
            return row is not None

    def list_conversations(self, query: str = "", limit: int = 100) -> list[dict[str, Any]]:
        query = query.strip()
        with self._connection() as conn:
            if query:
                like = f"%{query}%"
                rows = conn.execute("""
                    SELECT DISTINCT c.id, c.title, c.created_at, c.updated_at, c.is_pinned
                    FROM conversations c
                    LEFT JOIN messages m ON m.conversation_id=c.id
                    WHERE c.title LIKE ? OR m.content LIKE ?
                    ORDER BY c.is_pinned DESC, c.updated_at DESC LIMIT ?
                """, (like, like, limit)).fetchall()
            else:
                rows = conn.execute(
                    "SELECT id, title, created_at, updated_at, is_pinned FROM conversations ORDER BY is_pinned DESC, updated_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
        return [dict(row) for row in rows]

    def get_messages(self, conversation_id: int) -> list[dict[str, Any]]:
        with self._connection() as conn:
            rows = conn.execute("""
                SELECT id, role, content, created_at, model, duration_ms
                FROM messages WHERE conversation_id=? ORDER BY id
            """, (conversation_id,)).fetchall()
        return [dict(row) for row in rows]

    def add_message(
        self, conversation_id: int, role: str, content: str,
        model: str | None = None, duration_ms: int | None = None,
    ) -> int:
        now = time.time()
        with self._connection() as conn:
            cur = conn.execute("""
                INSERT INTO messages(conversation_id, role, content, created_at, model, duration_ms)
                VALUES (?, ?, ?, ?, ?, ?)
            """, (conversation_id, role, content, now, model, duration_ms))
            conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                (now, conversation_id),
            )
            if role == "user":
                row = conn.execute(
                    "SELECT title FROM conversations WHERE id=?", (conversation_id,)
                ).fetchone()
                if row and row["title"] == "Новый диалог":
                    title = content.strip().replace("\n", " ")[:48]
                    if title:
                        conn.execute(
                            "UPDATE conversations SET title=? WHERE id=?",
                            (title, conversation_id),
                        )
            return int(cur.lastrowid)

    def set_pinned(self, conversation_id: int, pinned: bool) -> None:
        with self._connection() as conn:
            conn.execute(
                "UPDATE conversations SET is_pinned=?, updated_at=? WHERE id=?",
                (1 if pinned else 0, time.time(), conversation_id),
            )

    def edit_user_message(
        self, conversation_id: int, message_id: int, content: str
    ) -> bool:
        """Edit one user message and discard later turns that depended on it."""
        content = content.strip()
        if not content:
            return False
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM messages "
                "WHERE id=? AND conversation_id=? AND role='user'",
                (int(message_id), int(conversation_id)),
            ).fetchone()
            if row is None:
                return False

            now = time.time()
            conn.execute(
                "UPDATE messages SET content=?, created_at=? "
                "WHERE id=? AND conversation_id=?",
                (content, now, int(message_id), int(conversation_id)),
            )
            # Responses and questions after this message depend on its old content.
            conn.execute(
                "DELETE FROM messages WHERE conversation_id=? AND id>?",
                (int(conversation_id), int(message_id)),
            )
            conn.execute(
                "UPDATE conversations SET updated_at=? WHERE id=?",
                (now, int(conversation_id)),
            )
            return True

    def edit_last_user_message(self, conversation_id: int, content: str) -> bool:
        """Backward-compatible helper for editing the most recent user message."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM messages "
                "WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 1",
                (int(conversation_id),),
            ).fetchone()
        if row is None:
            return False
        return self.edit_user_message(conversation_id, int(row["id"]), content)

    def clear_conversation(self, conversation_id: int) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM messages WHERE conversation_id=?", (conversation_id,))
            conn.execute("UPDATE conversations SET updated_at=? WHERE id=?", (time.time(), conversation_id))

    def rename_conversation(self, conversation_id: int, title: str) -> None:
        title = title.strip()[:100]
        if not title:
            raise ValueError("Название диалога не может быть пустым.")
        with self._connection() as conn:
            conn.execute(
                "UPDATE conversations SET title=?, updated_at=? WHERE id=?",
                (title, time.time(), conversation_id),
            )

    def delete_conversation(self, conversation_id: int) -> None:
        with self._connection() as conn:
            conn.execute("DELETE FROM conversations WHERE id=?", (conversation_id,))

    def remove_last_assistant_message(self, conversation_id: int) -> None:
        """Remove the most recent assistant answer (legacy helper)."""
        with self._connection() as conn:
            row = conn.execute(
                "SELECT id FROM messages WHERE conversation_id=? AND role='assistant' ORDER BY id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
            if row:
                conn.execute("DELETE FROM messages WHERE id=?", (row["id"],))

    def remove_last_response_message(self, conversation_id: int) -> None:
        """Remove the latest answer/error belonging to the latest user turn."""
        with self._connection() as conn:
            row = conn.execute(
                """
                SELECT id FROM messages
                WHERE conversation_id=?
                  AND role IN ('assistant', 'error')
                  AND id > COALESCE(
                      (SELECT MAX(id) FROM messages
                       WHERE conversation_id=? AND role='user'),
                      0
                  )
                ORDER BY id DESC LIMIT 1
                """,
                (conversation_id, conversation_id),
            ).fetchone()
            if row:
                conn.execute(
                    "DELETE FROM messages WHERE id=? AND conversation_id=?",
                    (row["id"], conversation_id),
                )

    def context_messages(self, conversation_id: int, limit: int = 16) -> list[dict[str, str]]:
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT role, content FROM messages WHERE conversation_id=? AND role IN ('user','assistant') ORDER BY id DESC LIMIT ?",
                (conversation_id, max(1, limit)),
            ).fetchall()
        return [{"role": row["role"], "content": row["content"]} for row in reversed(rows)]

    def last_user_message(self, conversation_id: int) -> str | None:
        with self._connection() as conn:
            row = conn.execute(
                "SELECT content FROM messages WHERE conversation_id=? AND role='user' ORDER BY id DESC LIMIT 1",
                (conversation_id,),
            ).fetchone()
        return str(row["content"]) if row else None
