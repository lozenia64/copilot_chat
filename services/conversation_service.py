from __future__ import annotations

import json
import logging
import os
import re
import secrets
import sqlite3
import time
from collections.abc import AsyncIterator, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from fastapi import Request


DEFAULT_CHAT_HISTORY_DB_PATH = Path(__file__).resolve().parent.parent / ".copilot_chat_history.sqlite3"
DEFAULT_CONVERSATION_TITLE = "새 대화"
DEFAULT_ASSISTANT_STATE = "complete"
DEFAULT_CHAT_HISTORY_TTL_SECONDS = 60 * 60 * 24 * 7
DEFAULT_CHAT_HISTORY_CLEANUP_INTERVAL_SECONDS = 60
MAX_CONVERSATION_TITLE_LENGTH = 80
MAX_ATTACHMENTS_PER_MESSAGE = 5
LOGGER = logging.getLogger(__name__)


@dataclass(slots=True)
class ConversationTurn:
    model: str
    prior_messages: list[dict[str, Any]]
    new_user_message: dict[str, Any]
    assistant_message_id: str

    @property
    def visible_messages(self) -> list[dict[str, Any]]:
        return [*self.prior_messages, self.new_user_message]


class ConversationStateError(Exception):
    def __init__(self, status_code: int, message: str, code: str) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code


class ChatHistoryRepository:
    def __init__(
        self,
        db_path: str | os.PathLike[str],
        *,
        ttl_seconds: int = DEFAULT_CHAT_HISTORY_TTL_SECONDS,
        cleanup_interval_seconds: int = DEFAULT_CHAT_HISTORY_CLEANUP_INTERVAL_SECONDS,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.ttl_seconds = max(int(ttl_seconds), 1)
        self.cleanup_interval_seconds = max(int(cleanup_interval_seconds), 0)
        self._last_cleanup_monotonic = 0.0
        self._initialize()

    def get_state_payload(self, scope_id: str) -> dict[str, Any]:
        current_time = time.time()
        with self._connection() as connection:
            self._maybe_purge_expired_history(connection, current_time)
            self._ensure_scope(connection, scope_id, current_time)
            scope_row = connection.execute(
                """
                SELECT active_conversation_id
                FROM conversation_scopes
                WHERE scope_id = ?
                """,
                (scope_id,),
            ).fetchone()
            conversation_rows = connection.execute(
                """
                SELECT id, title, model, created_at, updated_at
                FROM conversations
                WHERE scope_id = ?
                ORDER BY updated_at DESC, created_at DESC, id DESC
                """,
                (scope_id,),
            ).fetchall()
            messages_by_conversation = self._load_visible_messages(
                connection,
                [str(row["id"]) for row in conversation_rows],
            )
            conversation_ids = [str(row["id"]) for row in conversation_rows]
            active_conversation_id = (
                None if scope_row is None else self._normalize_text(scope_row["active_conversation_id"])
            )
            if active_conversation_id not in conversation_ids:
                active_conversation_id = conversation_ids[0] if conversation_ids else None
                connection.execute(
                    """
                    UPDATE conversation_scopes
                    SET active_conversation_id = ?, updated_at = ?
                    WHERE scope_id = ?
                    """,
                    (active_conversation_id, current_time, scope_id),
                )

        return {
            "sessions": [
                self._conversation_row_to_payload(
                    row,
                    messages_by_conversation.get(str(row["id"]), []),
                )
                for row in conversation_rows
            ],
            "activeSessionId": active_conversation_id,
        }

    def create_conversation(self, scope_id: str, model: str) -> dict[str, Any]:
        current_time = time.time()
        conversation_id = f"conv_{secrets.token_urlsafe(12)}"

        with self._connection() as connection:
            self._maybe_purge_expired_history(connection, current_time)
            self._ensure_scope(connection, scope_id, current_time)
            connection.execute(
                """
                INSERT INTO conversations (
                    id,
                    scope_id,
                    title,
                    model,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    conversation_id,
                    scope_id,
                    DEFAULT_CONVERSATION_TITLE,
                    model,
                    current_time,
                    current_time,
                ),
            )
            connection.execute(
                """
                UPDATE conversation_scopes
                SET active_conversation_id = ?, updated_at = ?
                WHERE scope_id = ?
                """,
                (conversation_id, current_time, scope_id),
            )

        return self.get_conversation_payload(scope_id, conversation_id)

    def get_conversation_payload(self, scope_id: str, conversation_id: str) -> dict[str, Any]:
        with self._connection() as connection:
            self._maybe_purge_expired_history(connection, time.time())
            conversation_row = self._require_conversation(connection, scope_id, conversation_id)
            messages = self._load_visible_messages(connection, [conversation_id]).get(conversation_id, [])
        return self._conversation_row_to_payload(conversation_row, messages)

    def activate_conversation(self, scope_id: str, conversation_id: str) -> None:
        current_time = time.time()
        with self._connection() as connection:
            self._maybe_purge_expired_history(connection, current_time)
            self._require_conversation(connection, scope_id, conversation_id)
            self._ensure_scope(connection, scope_id, current_time)
            connection.execute(
                """
                UPDATE conversation_scopes
                SET active_conversation_id = ?, updated_at = ?
                WHERE scope_id = ?
                """,
                (conversation_id, current_time, scope_id),
            )

    def update_conversation_model(self, scope_id: str, conversation_id: str, model: str) -> dict[str, Any]:
        with self._connection() as connection:
            self._maybe_purge_expired_history(connection, time.time())
            self._require_conversation(connection, scope_id, conversation_id)
            connection.execute(
                """
                UPDATE conversations
                SET model = ?
                WHERE id = ? AND scope_id = ?
                """,
                (model, conversation_id, scope_id),
            )

        return self.get_conversation_payload(scope_id, conversation_id)

    def update_conversation_title(self, scope_id: str, conversation_id: str, title: str) -> dict[str, Any]:
        current_time = time.time()
        with self._connection() as connection:
            self._maybe_purge_expired_history(connection, current_time)
            self._require_conversation(connection, scope_id, conversation_id)
            connection.execute(
                """
                UPDATE conversations
                SET title = ?, updated_at = ?
                WHERE id = ? AND scope_id = ?
                """,
                (title, current_time, conversation_id, scope_id),
            )

        return self.get_conversation_payload(scope_id, conversation_id)

    def delete_conversation(self, scope_id: str, conversation_id: str) -> dict[str, Any]:
        current_time = time.time()
        with self._connection() as connection:
            self._maybe_purge_expired_history(connection, current_time)
            self._require_conversation(connection, scope_id, conversation_id)
            connection.execute(
                """
                DELETE FROM conversations
                WHERE id = ? AND scope_id = ?
                """,
                (conversation_id, scope_id),
            )
            scope_row = connection.execute(
                """
                SELECT active_conversation_id
                FROM conversation_scopes
                WHERE scope_id = ?
                """,
                (scope_id,),
            ).fetchone()
            if scope_row is not None and self._normalize_text(scope_row["active_conversation_id"]) == conversation_id:
                next_active_row = connection.execute(
                    """
                    SELECT id
                    FROM conversations
                    WHERE scope_id = ?
                    ORDER BY updated_at DESC, created_at DESC, id DESC
                    LIMIT 1
                    """,
                    (scope_id,),
                ).fetchone()
                next_active_id = None if next_active_row is None else str(next_active_row["id"])
                connection.execute(
                    """
                    UPDATE conversation_scopes
                    SET active_conversation_id = ?, updated_at = ?
                    WHERE scope_id = ?
                    """,
                    (next_active_id, current_time, scope_id),
                )

        return self.get_state_payload(scope_id)

    def create_attachment(
        self,
        scope_id: str,
        conversation_id: str,
        *,
        attachment_id: str | None = None,
        original_filename: str,
        mime_type: str,
        byte_size: int,
        width: int,
        height: int,
        storage_path: str,
    ) -> dict[str, Any]:
        current_time = time.time()
        attachment_id = attachment_id or f"att_{secrets.token_urlsafe(12)}"

        with self._connection() as connection:
            self._maybe_purge_expired_history(connection, current_time)
            self._require_conversation(connection, scope_id, conversation_id)
            connection.execute(
                """
                INSERT INTO conversation_attachments (
                    id,
                    scope_id,
                    conversation_id,
                    message_id,
                    original_filename,
                    mime_type,
                    byte_size,
                    width,
                    height,
                    storage_path,
                    status,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    attachment_id,
                    scope_id,
                    conversation_id,
                    None,
                    original_filename,
                    mime_type,
                    byte_size,
                    width,
                    height,
                    storage_path,
                    "uploaded",
                    current_time,
                    current_time,
                ),
            )
            row = self._require_attachment(connection, scope_id, conversation_id, attachment_id)
            connection.execute(
                """
                UPDATE conversations
                SET updated_at = ?
                WHERE id = ? AND scope_id = ?
                """,
                (current_time, conversation_id, scope_id),
            )

        payload = self._attachment_row_to_payload(row)
        payload["createdAt"] = float(row["created_at"])
        return payload

    def get_attachment_record(
        self,
        scope_id: str,
        conversation_id: str,
        attachment_id: str,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            self._maybe_purge_expired_history(connection, time.time())
            row = self._require_attachment(connection, scope_id, conversation_id, attachment_id)
        return self._attachment_row_to_internal_payload(row)

    def get_attachment_record_for_content(
        self,
        scope_id: str,
        conversation_id: str,
        attachment_id: str,
    ) -> dict[str, Any]:
        with self._connection() as connection:
            self._maybe_purge_expired_history(connection, time.time())
            row = self._require_attachment(connection, scope_id, conversation_id, attachment_id)
        return self._attachment_row_to_internal_payload(row)

    def delete_pending_attachment(
        self,
        scope_id: str,
        conversation_id: str,
        attachment_id: str,
    ) -> dict[str, Any]:
        current_time = time.time()
        with self._connection() as connection:
            self._maybe_purge_expired_history(connection, current_time)
            row = self._require_attachment(connection, scope_id, conversation_id, attachment_id)
            if row["message_id"] is not None:
                raise ConversationStateError(
                    status_code=409,
                    message="이미 전송에 사용된 첨부 이미지입니다. 다시 업로드하세요.",
                    code="attachment_already_attached",
                )
            payload = self._attachment_row_to_internal_payload(row)
            connection.execute(
                """
                DELETE FROM conversation_attachments
                WHERE id = ? AND conversation_id = ? AND scope_id = ? AND message_id IS NULL
                """,
                (attachment_id, conversation_id, scope_id),
            )
            connection.execute(
                """
                UPDATE conversations
                SET updated_at = ?
                WHERE id = ? AND scope_id = ?
                """,
                (current_time, conversation_id, scope_id),
            )
        return payload

    def begin_turn(
        self,
        scope_id: str,
        conversation_id: str,
        *,
        content: str,
        attachment_ids: list[str] | None = None,
        model: str | None,
    ) -> ConversationTurn:
        current_time = time.time()
        assistant_message_id = f"msg_{secrets.token_urlsafe(12)}"
        normalized_attachment_ids = list(dict.fromkeys(attachment_ids or []))

        with self._connection() as connection:
            self._maybe_purge_expired_history(connection, current_time)
            conversation_row = self._require_conversation(connection, scope_id, conversation_id)
            connection.execute(
                """
                DELETE FROM conversation_messages
                WHERE conversation_id = ? AND role = 'assistant' AND content = ''
                """,
                (conversation_id,),
            )
            visible_messages = self._load_visible_messages(connection, [conversation_id]).get(conversation_id, [])
            model_id = model or str(conversation_row["model"])
            attachments = self._require_pending_attachments(
                connection,
                scope_id,
                conversation_id,
                normalized_attachment_ids,
            )
            next_ordinal = self._next_ordinal(connection, conversation_id)
            user_message_id = f"msg_{secrets.token_urlsafe(12)}"
            connection.execute(
                """
                INSERT INTO conversation_messages (
                    id,
                    conversation_id,
                    role,
                    content,
                    ordinal,
                    state,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    user_message_id,
                    conversation_id,
                    "user",
                    content,
                    next_ordinal,
                    DEFAULT_ASSISTANT_STATE,
                    current_time,
                    current_time,
                ),
            )
            connection.execute(
                """
                INSERT INTO conversation_messages (
                    id,
                    conversation_id,
                    role,
                    content,
                    ordinal,
                    state,
                    created_at,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    assistant_message_id,
                    conversation_id,
                    "assistant",
                    "",
                    next_ordinal + 1,
                    "streaming",
                    current_time,
                    current_time,
                ),
            )
            if attachments:
                placeholders = ", ".join("?" for _ in attachments)
                connection.execute(
                    f"""
                    UPDATE conversation_attachments
                    SET message_id = ?, status = 'attached', updated_at = ?
                    WHERE id IN ({placeholders})
                    """,
                    (user_message_id, current_time, *(str(row["id"]) for row in attachments)),
                )
            has_existing_user_message = any(message["role"] == "user" for message in visible_messages)
            title = str(conversation_row["title"])
            if not has_existing_user_message and title == DEFAULT_CONVERSATION_TITLE:
                if content:
                    title = self._derive_conversation_title(content)
                elif attachments:
                    title = self._derive_attachment_title(len(attachments))
            connection.execute(
                """
                UPDATE conversations
                SET title = ?, model = ?, updated_at = ?
                WHERE id = ? AND scope_id = ?
                """,
                (title, model_id, current_time, conversation_id, scope_id),
            )
            connection.execute(
                """
                UPDATE conversation_scopes
                SET active_conversation_id = ?, updated_at = ?
                WHERE scope_id = ?
                """,
                (conversation_id, current_time, scope_id),
            )

        return ConversationTurn(
            model=model_id,
            prior_messages=visible_messages,
            new_user_message={
                "role": "user",
                "content": content,
                "attachments": [
                    self._attachment_row_to_internal_payload(row)
                    for row in attachments
                ],
            },
            assistant_message_id=assistant_message_id,
        )

    def update_assistant_message(
        self,
        scope_id: str,
        conversation_id: str,
        assistant_message_id: str,
        *,
        content: str,
        state: str,
    ) -> None:
        current_time = time.time()
        with self._connection() as connection:
            self._maybe_purge_expired_history(connection, current_time)
            self._require_assistant_message(connection, scope_id, conversation_id, assistant_message_id)
            connection.execute(
                """
                UPDATE conversation_messages
                SET content = ?, state = ?, updated_at = ?
                WHERE id = ?
                """,
                (content, state, current_time, assistant_message_id),
            )
            connection.execute(
                """
                UPDATE conversations
                SET updated_at = ?
                WHERE id = ? AND scope_id = ?
                """,
                (current_time, conversation_id, scope_id),
            )

    def finalize_assistant_message(
        self,
        scope_id: str,
        conversation_id: str,
        assistant_message_id: str,
        *,
        content: str,
        state: str,
    ) -> None:
        if not content:
            self.delete_message(scope_id, conversation_id, assistant_message_id)
            return

        self.update_assistant_message(
            scope_id,
            conversation_id,
            assistant_message_id,
            content=content,
            state=state,
        )

    def delete_message(self, scope_id: str, conversation_id: str, message_id: str) -> None:
        current_time = time.time()
        with self._connection() as connection:
            self._maybe_purge_expired_history(connection, current_time)
            self._require_conversation(connection, scope_id, conversation_id)
            connection.execute(
                """
                DELETE FROM conversation_messages
                WHERE id = ? AND conversation_id = ?
                """,
                (message_id, conversation_id),
            )
            connection.execute(
                """
                UPDATE conversations
                SET updated_at = ?
                WHERE id = ? AND scope_id = ?
                """,
                (current_time, conversation_id, scope_id),
            )

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
        connection.execute("PRAGMA foreign_keys = ON")
        return connection

    @contextmanager
    def _connection(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connection() as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_scopes (
                    scope_id TEXT PRIMARY KEY,
                    active_conversation_id TEXT,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    scope_id TEXT NOT NULL,
                    title TEXT NOT NULL,
                    model TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(scope_id) REFERENCES conversation_scopes(scope_id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversations_scope_updated
                ON conversations (scope_id, updated_at DESC, created_at DESC)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversations_updated_at
                ON conversations (updated_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_messages (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL,
                    role TEXT NOT NULL,
                    content TEXT NOT NULL,
                    ordinal INTEGER NOT NULL,
                    state TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
                )
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS conversation_attachments (
                    id TEXT PRIMARY KEY,
                    scope_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    message_id TEXT,
                    original_filename TEXT NOT NULL,
                    mime_type TEXT NOT NULL,
                    byte_size INTEGER NOT NULL,
                    width INTEGER NOT NULL,
                    height INTEGER NOT NULL,
                    storage_path TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    updated_at REAL NOT NULL,
                    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
                    FOREIGN KEY(message_id) REFERENCES conversation_messages(id) ON DELETE SET NULL
                )
                """
            )
            connection.execute(
                """
                CREATE UNIQUE INDEX IF NOT EXISTS idx_conversation_messages_ordinal
                ON conversation_messages (conversation_id, ordinal)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_messages_visible
                ON conversation_messages (conversation_id, ordinal, updated_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_attachments_conversation
                ON conversation_attachments (conversation_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_attachments_message
                ON conversation_attachments (message_id, created_at)
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_conversation_attachments_scope_status
                ON conversation_attachments (scope_id, status, created_at)
                """
            )

    def _ensure_scope(self, connection: sqlite3.Connection, scope_id: str, current_time: float) -> None:
        connection.execute(
            """
            INSERT OR IGNORE INTO conversation_scopes (
                scope_id,
                active_conversation_id,
                created_at,
                updated_at
            ) VALUES (?, ?, ?, ?)
            """,
            (scope_id, None, current_time, current_time),
        )

    def _maybe_purge_expired_history(self, connection: sqlite3.Connection, current_time: float) -> None:
        if self.cleanup_interval_seconds > 0:
            now_monotonic = time.monotonic()
            if self._last_cleanup_monotonic and (
                now_monotonic - self._last_cleanup_monotonic
            ) < self.cleanup_interval_seconds:
                return
        else:
            now_monotonic = 0.0

        cutoff = current_time - self.ttl_seconds
        expired_conversation_rows = connection.execute(
            """
            SELECT id
            FROM conversations
            WHERE updated_at <= ?
            """,
            (cutoff,),
        ).fetchall()
        expired_conversation_ids = [str(row["id"]) for row in expired_conversation_rows]

        if expired_conversation_ids:
            placeholders = ", ".join("?" for _ in expired_conversation_ids)
            expired_attachment_rows = connection.execute(
                f"""
                SELECT conversation_id, storage_path
                FROM conversation_attachments
                WHERE conversation_id IN ({placeholders})
                """,
                tuple(expired_conversation_ids),
            ).fetchall()
            for row in expired_attachment_rows:
                conversation_id = str(row["conversation_id"])
                storage_path = self._normalize_text(row["storage_path"])
                if not storage_path:
                    continue
                try:
                    Path(storage_path).unlink(missing_ok=True)
                except OSError:
                    LOGGER.warning(
                        "Failed to delete expired attachment file during cleanup: conversation_id=%s storage_path=%s",
                        conversation_id,
                        storage_path,
                        exc_info=True,
                    )

            if expired_conversation_ids:
                placeholders = ", ".join("?" for _ in expired_conversation_ids)
                connection.execute(
                    f"""
                    DELETE FROM conversations
                    WHERE id IN ({placeholders})
                    """,
                    tuple(expired_conversation_ids),
                )

        connection.execute(
            """
            DELETE FROM conversation_scopes
            WHERE updated_at <= ?
              AND NOT EXISTS (
                  SELECT 1
                  FROM conversations
                  WHERE conversations.scope_id = conversation_scopes.scope_id
              )
            """,
            (cutoff,),
        )
        self._last_cleanup_monotonic = now_monotonic

    def _load_visible_messages(
        self,
        connection: sqlite3.Connection,
        conversation_ids: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {conversation_id: [] for conversation_id in conversation_ids}
        if not conversation_ids:
            return grouped

        placeholders = ", ".join("?" for _ in conversation_ids)
        rows = connection.execute(
            f"""
            SELECT id, conversation_id, role, content, state, created_at, updated_at
            FROM conversation_messages
            WHERE conversation_id IN ({placeholders})
              AND (
                    content != ''
                    OR EXISTS (
                        SELECT 1
                        FROM conversation_attachments
                        WHERE conversation_attachments.message_id = conversation_messages.id
                    )
                  )
            ORDER BY conversation_id ASC, ordinal ASC, created_at ASC, id ASC
            """,
            tuple(conversation_ids),
        ).fetchall()
        attachments_by_message_id = self._load_attachments_by_message_ids(
            connection,
            [str(row["id"]) for row in rows],
        )
        for row in rows:
            conversation_id = str(row["conversation_id"])
            grouped.setdefault(conversation_id, []).append(
                self._message_row_to_payload(
                    row,
                    attachments_by_message_id.get(str(row["id"]), []),
                )
            )
        return grouped

    def _load_attachments_by_message_ids(
        self,
        connection: sqlite3.Connection,
        message_ids: list[str],
    ) -> dict[str, list[dict[str, Any]]]:
        grouped: dict[str, list[dict[str, Any]]] = {message_id: [] for message_id in message_ids}
        if not message_ids:
            return grouped

        placeholders = ", ".join("?" for _ in message_ids)
        rows = connection.execute(
            f"""
            SELECT id, scope_id, conversation_id, message_id, original_filename, mime_type,
                   byte_size, width, height, storage_path, status, created_at, updated_at
            FROM conversation_attachments
            WHERE message_id IN ({placeholders})
            ORDER BY created_at ASC, id ASC
            """,
            tuple(message_ids),
        ).fetchall()
        for row in rows:
            grouped.setdefault(str(row["message_id"]), []).append(self._attachment_row_to_payload(row))
        return grouped

    def _require_conversation(
        self,
        connection: sqlite3.Connection,
        scope_id: str,
        conversation_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT id, scope_id, title, model, created_at, updated_at
            FROM conversations
            WHERE id = ? AND scope_id = ?
            """,
            (conversation_id, scope_id),
        ).fetchone()
        if row is None:
            raise ConversationStateError(
                status_code=404,
                message="대화 세션을 찾을 수 없습니다. 새 대화를 시작하세요.",
                code="conversation_not_found",
            )
        return row

    def _require_assistant_message(
        self,
        connection: sqlite3.Connection,
        scope_id: str,
        conversation_id: str,
        assistant_message_id: str,
    ) -> None:
        row = connection.execute(
            """
            SELECT conversation_messages.id
            FROM conversation_messages
            JOIN conversations ON conversations.id = conversation_messages.conversation_id
            WHERE conversation_messages.id = ?
              AND conversation_messages.conversation_id = ?
              AND conversations.scope_id = ?
              AND conversation_messages.role = 'assistant'
            """,
            (assistant_message_id, conversation_id, scope_id),
        ).fetchone()
        if row is None:
            raise ConversationStateError(
                status_code=404,
                message="대화 세션을 찾을 수 없습니다. 새 대화를 시작하세요.",
                code="conversation_not_found",
            )

    def _require_attachment(
        self,
        connection: sqlite3.Connection,
        scope_id: str,
        conversation_id: str,
        attachment_id: str,
    ) -> sqlite3.Row:
        row = connection.execute(
            """
            SELECT id, scope_id, conversation_id, message_id, original_filename, mime_type,
                   byte_size, width, height, storage_path, status, created_at, updated_at
            FROM conversation_attachments
            WHERE id = ? AND scope_id = ? AND conversation_id = ?
            """,
            (attachment_id, scope_id, conversation_id),
        ).fetchone()
        if row is None:
            raise ConversationStateError(
                status_code=404,
                message="첨부 이미지를 찾을 수 없습니다. 다시 업로드하세요.",
                code="attachment_not_found",
            )
        return row

    def _require_pending_attachments(
        self,
        connection: sqlite3.Connection,
        scope_id: str,
        conversation_id: str,
        attachment_ids: list[str],
    ) -> list[sqlite3.Row]:
        if not attachment_ids:
            return []

        if len(attachment_ids) > MAX_ATTACHMENTS_PER_MESSAGE:
            raise ConversationStateError(
                status_code=400,
                message="이미지는 한 번에 최대 5개까지 전송할 수 있습니다.",
                code="conversation_attachments_limit_exceeded",
            )

        rows = [
            self._require_attachment(connection, scope_id, conversation_id, attachment_id)
            for attachment_id in attachment_ids
        ]
        for row in rows:
            if row["message_id"] is not None:
                raise ConversationStateError(
                    status_code=409,
                    message="이미 전송에 사용된 첨부 이미지입니다. 다시 업로드하세요.",
                    code="attachment_already_attached",
                )
        return rows

    def _next_ordinal(self, connection: sqlite3.Connection, conversation_id: str) -> int:
        row = connection.execute(
            """
            SELECT COALESCE(MAX(ordinal), -1) AS max_ordinal
            FROM conversation_messages
            WHERE conversation_id = ?
            """,
            (conversation_id,),
        ).fetchone()
        if row is None:
            return 0
        return int(row["max_ordinal"]) + 1

    def _conversation_row_to_payload(
        self,
        row: sqlite3.Row,
        messages: list[dict[str, Any]],
    ) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "title": str(row["title"]),
            "model": str(row["model"]),
            "messages": messages,
            "createdAt": float(row["created_at"]),
            "updatedAt": float(row["updated_at"]),
        }

    def _message_row_to_payload(
        self,
        row: sqlite3.Row,
        attachments: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "role": str(row["role"]),
            "content": str(row["content"]),
            "attachments": list(attachments or []),
            "status": str(row["state"]),
            "createdAt": float(row["created_at"]),
            "updatedAt": float(row["updated_at"]),
        }

    def _attachment_row_to_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": str(row["id"]),
            "fileName": str(row["original_filename"]),
            "mimeType": str(row["mime_type"]),
            "byteSize": int(row["byte_size"]),
            "width": int(row["width"]),
            "height": int(row["height"]),
        }

    def _attachment_row_to_internal_payload(self, row: sqlite3.Row) -> dict[str, Any]:
        payload = self._attachment_row_to_payload(row)
        payload.update(
            {
                "conversationId": str(row["conversation_id"]),
                "messageId": None if row["message_id"] is None else str(row["message_id"]),
                "status": str(row["status"]),
                "storagePath": str(row["storage_path"]),
                "createdAt": float(row["created_at"]),
                "updatedAt": float(row["updated_at"]),
            }
        )
        return payload

    def _derive_conversation_title(self, content: str) -> str:
        normalized = re.sub(r"\s+", " ", content).strip()
        if not normalized:
            return DEFAULT_CONVERSATION_TITLE
        if len(normalized) <= 42:
            return normalized
        return f"{normalized[:42].rstrip()}..."

    def _derive_attachment_title(self, attachment_count: int) -> str:
        if attachment_count <= 0:
            return DEFAULT_CONVERSATION_TITLE
        if attachment_count == 1:
            return "이미지 1개"
        return f"이미지 {attachment_count}개"

    def _normalize_text(self, value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None


class ConversationService:
    def __init__(
        self,
        history_db_path: str | os.PathLike[str] | None = None,
        *,
        ttl_seconds: int = DEFAULT_CHAT_HISTORY_TTL_SECONDS,
        cleanup_interval_seconds: int = DEFAULT_CHAT_HISTORY_CLEANUP_INTERVAL_SECONDS,
    ) -> None:
        configured_history_db_path = history_db_path or os.getenv("COPILOT_CHAT_HISTORY_DB_PATH")
        self.history_db_path = (
            Path(configured_history_db_path)
            if configured_history_db_path
            else DEFAULT_CHAT_HISTORY_DB_PATH
        )
        self._repository = ChatHistoryRepository(
            self.history_db_path,
            ttl_seconds=ttl_seconds,
            cleanup_interval_seconds=cleanup_interval_seconds,
        )

    def get_state_payload(self, scope_id: str) -> dict[str, Any]:
        return self._repository.get_state_payload(scope_id)

    def create_conversation(self, scope_id: str, model: str) -> dict[str, Any]:
        return self._repository.create_conversation(scope_id, model)

    def get_conversation_payload(self, scope_id: str, conversation_id: str) -> dict[str, Any]:
        return self._repository.get_conversation_payload(scope_id, conversation_id)

    def activate_conversation(self, scope_id: str, conversation_id: str) -> None:
        self._repository.activate_conversation(scope_id, conversation_id)

    def update_conversation_model(self, scope_id: str, conversation_id: str, model: str) -> dict[str, Any]:
        return self._repository.update_conversation_model(
            scope_id,
            conversation_id,
            model,
        )

    def validate_conversation_title(self, title: Any) -> str:
        if not isinstance(title, str) or not title.strip():
            raise ConversationStateError(
                status_code=400,
                message="대화 제목을 입력하세요.",
                code="conversation_title_required",
            )
        normalized = re.sub(r"\s+", " ", title).strip()
        if len(normalized) > MAX_CONVERSATION_TITLE_LENGTH:
            normalized = normalized[:MAX_CONVERSATION_TITLE_LENGTH].rstrip()
        return normalized

    def update_conversation_title(self, scope_id: str, conversation_id: str, title: str) -> dict[str, Any]:
        return self._repository.update_conversation_title(
            scope_id,
            conversation_id,
            title,
        )

    def delete_conversation(self, scope_id: str, conversation_id: str) -> dict[str, Any]:
        return self._repository.delete_conversation(scope_id, conversation_id)

    def create_attachment(
        self,
        scope_id: str,
        conversation_id: str,
        *,
        attachment_id: str | None = None,
        original_filename: str,
        mime_type: str,
        byte_size: int,
        width: int,
        height: int,
        storage_path: str,
    ) -> dict[str, Any]:
        return self._repository.create_attachment(
            scope_id,
            conversation_id,
            attachment_id=attachment_id,
            original_filename=original_filename,
            mime_type=mime_type,
            byte_size=byte_size,
            width=width,
            height=height,
            storage_path=storage_path,
        )

    def require_conversation(self, scope_id: str, conversation_id: str) -> None:
        current_time = time.time()
        with self._repository._connection() as connection:
            self._repository._maybe_purge_expired_history(connection, current_time)
            self._repository._require_conversation(connection, scope_id, conversation_id)

    def get_attachment_record(
        self,
        scope_id: str,
        conversation_id: str,
        attachment_id: str,
    ) -> dict[str, Any]:
        return self._repository.get_attachment_record(scope_id, conversation_id, attachment_id)

    def get_attachment_record_for_content(
        self,
        scope_id: str,
        conversation_id: str,
        attachment_id: str,
    ) -> dict[str, Any]:
        return self._repository.get_attachment_record_for_content(scope_id, conversation_id, attachment_id)

    def delete_pending_attachment(
        self,
        scope_id: str,
        conversation_id: str,
        attachment_id: str,
    ) -> dict[str, Any]:
        return self._repository.delete_pending_attachment(scope_id, conversation_id, attachment_id)

    def validate_message_input(self, content: Any, attachments: Any) -> tuple[str, list[str]]:
        if not isinstance(content, str):
            raise ConversationStateError(
                status_code=400,
                message="첨부 이미지 형식이 올바르지 않습니다.",
                code="conversation_attachments_invalid",
            )
        if not isinstance(attachments, list):
            raise ConversationStateError(
                status_code=400,
                message="첨부 이미지 형식이 올바르지 않습니다.",
                code="conversation_attachments_invalid",
            )

        normalized_content = content.strip()
        normalized_attachment_ids: list[str] = []
        for attachment in attachments:
            if not isinstance(attachment, dict):
                raise ConversationStateError(
                    status_code=400,
                    message="첨부 이미지 형식이 올바르지 않습니다.",
                    code="conversation_attachments_invalid",
                )
            attachment_id = self._repository._normalize_text(attachment.get("id"))
            if not attachment_id:
                raise ConversationStateError(
                    status_code=400,
                    message="첨부 이미지 형식이 올바르지 않습니다.",
                    code="conversation_attachments_invalid",
                )
            normalized_attachment_ids.append(attachment_id)

        normalized_attachment_ids = list(dict.fromkeys(normalized_attachment_ids))
        if len(normalized_attachment_ids) > MAX_ATTACHMENTS_PER_MESSAGE:
            raise ConversationStateError(
                status_code=400,
                message="이미지는 한 번에 최대 5개까지 전송할 수 있습니다.",
                code="conversation_attachments_limit_exceeded",
            )
        if not normalized_content and not normalized_attachment_ids:
            raise ConversationStateError(
                status_code=400,
                message="보낼 메시지 또는 이미지를 추가하세요.",
                code="conversation_message_required",
            )
        return normalized_content, normalized_attachment_ids

    def validate_message_content(self, content: str) -> str:
        normalized_content, _ = self.validate_message_input(content, [])
        return normalized_content

    def begin_turn(
        self,
        scope_id: str,
        conversation_id: str,
        *,
        content: str,
        attachment_ids: list[str] | None = None,
        model: str | None,
    ) -> ConversationTurn:
        return self._repository.begin_turn(
            scope_id,
            conversation_id,
            content=content,
            attachment_ids=attachment_ids,
            model=model,
        )

    async def persist_stream(
        self,
        *,
        request: Request,
        scope_id: str,
        conversation_id: str,
        assistant_message_id: str,
        stream: AsyncIterator[bytes],
    ) -> AsyncIterator[bytes]:
        assistant_content = ""
        saw_stream_error = False
        stream_error_message = ""

        try:
            async for chunk in stream:
                payload = self._extract_sse_payload(chunk)
                if payload == "[DONE]":
                    yield chunk
                    continue

                if isinstance(payload, dict):
                    chunk_text = self._extract_stream_text(payload)
                    if chunk_text:
                        assistant_content += chunk_text
                        self._repository.update_assistant_message(
                            scope_id,
                            conversation_id,
                            assistant_message_id,
                            content=assistant_content,
                            state="streaming",
                        )
                    elif self._is_stream_error_payload(payload):
                        saw_stream_error = True
                        if not assistant_content:
                            stream_error_message = self._extract_stream_error_message(payload)

                yield chunk
        finally:
            is_disconnected = await request.is_disconnected()
            final_content = assistant_content or stream_error_message
            if not final_content:
                self._repository.delete_message(scope_id, conversation_id, assistant_message_id)
                return

            final_state = DEFAULT_ASSISTANT_STATE
            if is_disconnected:
                final_state = "aborted"
            elif saw_stream_error:
                final_state = "partial"

            self._repository.finalize_assistant_message(
                scope_id,
                conversation_id,
                assistant_message_id,
                content=final_content,
                state=final_state,
            )

    def _extract_sse_payload(self, chunk: bytes) -> dict[str, Any] | str | None:
        text = chunk.decode("utf-8", errors="ignore")
        data_lines = [
            line[5:].strip()
            for line in text.splitlines()
            if line.startswith("data:")
        ]
        if not data_lines:
            return None

        data = "\n".join(data_lines)
        if data == "[DONE]":
            return data

        try:
            payload = json.loads(data)
        except json.JSONDecodeError:
            return None
        if isinstance(payload, dict):
            return payload
        return None

    def _is_stream_error_payload(self, payload: dict[str, Any]) -> bool:
        return payload.get("code") in {
            "copilot_chat_stream_failed",
            "copilot_model_not_supported",
            "copilot_rate_limit_exceeded",
        }

    def _extract_stream_error_message(self, payload: dict[str, Any]) -> str:
        message = payload.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()

        detail = payload.get("detail")
        if isinstance(detail, str) and detail.strip():
            return detail.strip()

        return ""

    def _extract_stream_text(self, payload: dict[str, Any]) -> str:
        choices = payload.get("choices")
        if not isinstance(choices, list) or not choices:
            return ""

        choice = choices[0]
        if not isinstance(choice, dict):
            return ""

        delta = choice.get("delta")
        if not isinstance(delta, dict):
            delta = choice.get("message")
        if not isinstance(delta, dict):
            return ""

        content = self._coerce_stream_content(delta.get("content"))
        if content:
            return content

        reasoning_content = delta.get("reasoning_content")
        if isinstance(reasoning_content, str) and reasoning_content:
            return reasoning_content

        tool_calls = delta.get("tool_calls")
        if tool_calls:
            return json.dumps(tool_calls, ensure_ascii=False)

        return ""

    def _coerce_stream_content(self, content: Any) -> str:
        if isinstance(content, str):
            return content
        if not isinstance(content, list):
            return ""

        parts: list[str] = []
        for item in content:
            if isinstance(item, str):
                parts.append(item)
                continue
            if not isinstance(item, dict):
                continue

            text = item.get("text")
            if isinstance(text, str):
                parts.append(text)
                continue

            nested_content = item.get("content")
            if isinstance(nested_content, str):
                parts.append(nested_content)

        return "".join(parts)