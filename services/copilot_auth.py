from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import os
import re
import secrets
import sqlite3
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx
from cryptography.fernet import Fernet, InvalidToken
from fastapi import Request, Response


LOGGER = logging.getLogger(__name__)

DEFAULT_GITHUB_CLIENT_ID = "Iv1.b507a08c87ecfe98"
DEFAULT_GITHUB_DEVICE_CODE_URL = "https://github.com/login/device/code"
DEFAULT_GITHUB_ACCESS_TOKEN_URL = "https://github.com/login/oauth/access_token"
DEFAULT_GITHUB_API_KEY_URL = "https://api.github.com/copilot_internal/v2/token"
DEFAULT_GITHUB_USER_URL = "https://api.github.com/user"
DEFAULT_GITHUB_USAGE_URLS = (
    "https://api.github.com/copilot_internal/v2/usage",
    "https://api.github.com/copilot_internal/user",
)
DEFAULT_GITHUB_COPILOT_API_BASE = "https://api.githubcopilot.com"
DEFAULT_PENDING_LOGIN_DB_PATH = Path(__file__).resolve().parent.parent / ".copilot_pending_login.sqlite3"
DEFAULT_COMPLETED_LOGIN_TTL_SECONDS = 60
GITHUB_FORM_CONTENT_TYPE = "application/x-www-form-urlencoded"
USAGE_SOURCE_MAP = {
    DEFAULT_GITHUB_USAGE_URLS[0]: "copilot_usage_api",
    DEFAULT_GITHUB_USAGE_URLS[1]: "copilot_user_api",
}
USAGE_DETAIL_MESSAGES = {
    "not_authenticated": None,
    "copilot_usage_pending": None,
    "copilot_usage_ok": None,
    "copilot_usage_partial": "GitHub Copilot 사용량 정보 일부만 확인되었습니다.",
    "copilot_usage_unavailable": "GitHub Copilot 사용량 정보를 지금 확인할 수 없습니다.",
    "copilot_usage_auth_failed": "GitHub Copilot 사용량 정보를 확인하려면 다시 로그인하세요.",
    "copilot_usage_shape_unrecognized": "GitHub Copilot 사용량 응답 형식을 확인할 수 없습니다.",
}


@dataclass(slots=True)
class CopilotLoginTicket:
    session_binding: str
    device_code: str
    user_code: str
    verification_uri: str
    verification_uri_complete: str | None
    interval: int
    expires_at: float
    next_poll_at: float
    flow_id: str = ""
    version: int = 1


@dataclass(slots=True)
class CopilotLoginHandle:
    flow_id: str
    session_binding: str
    version: int


@dataclass(slots=True)
class CopilotCredentialSession:
    github_access_token: str
    copilot_api_token: str
    copilot_api_expires_at: float
    copilot_api_base: str
    issued_at: float
    updated_at: float
    credential_id: str
    github_user_id: str | None = None
    github_login: str | None = None


@dataclass(slots=True)
class HistoryOwnerContext:
    scope_id: str
    authenticated: bool
    github_user_id: str | None = None
    github_login: str | None = None


@dataclass(slots=True)
class CopilotCompletedLogin:
    session_binding: str
    credential_envelope: str
    credential_id: str
    copilot_token_expires_at: float
    replay_expires_at: float
    flow_id: str = ""
    version: int = 1


class CopilotAuthError(Exception):
    def __init__(
        self,
        status_code: int,
        message: str,
        code: str,
        *,
        log_context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.message = message
        self.code = code
        self.log_context = {} if log_context is None else log_context


class PendingLoginStore:
    def __init__(self, db_path: str | os.PathLike[str]) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def save(self, ticket: CopilotLoginTicket, *, now: float | None = None) -> CopilotLoginTicket:
        if not ticket.flow_id:
            raise ValueError("Pending login tickets require a flow_id before they can be stored.")

        current_time = time.time() if now is None else now
        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO pending_logins (
                    flow_id,
                    session_binding,
                    device_code,
                    user_code,
                    verification_uri,
                    verification_uri_complete,
                    interval_seconds,
                    expires_at,
                    next_poll_at,
                    version,
                    updated_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    ticket.flow_id,
                    ticket.session_binding,
                    ticket.device_code,
                    ticket.user_code,
                    ticket.verification_uri,
                    ticket.verification_uri_complete,
                    ticket.interval,
                    ticket.expires_at,
                    ticket.next_poll_at,
                    ticket.version,
                    current_time,
                ),
            )
        return ticket

    def get(self, flow_id: str) -> CopilotLoginTicket | None:
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    flow_id,
                    session_binding,
                    device_code,
                    user_code,
                    verification_uri,
                    verification_uri_complete,
                    interval_seconds,
                    expires_at,
                    next_poll_at,
                    version
                FROM pending_logins
                WHERE flow_id = ?
                """,
                (flow_id,),
            ).fetchone()
        if row is None:
            return None
        return self._row_to_ticket(row)

    def get_completed(
        self,
        flow_id: str,
        *,
        now: float | None = None,
    ) -> CopilotCompletedLogin | None:
        current_time = time.time() if now is None else now
        with self._connection() as connection:
            row = connection.execute(
                """
                SELECT
                    flow_id,
                    session_binding,
                    credential_envelope,
                    credential_id,
                    copilot_token_expires_at,
                    replay_expires_at,
                    version
                FROM completed_logins
                WHERE flow_id = ?
                """,
                (flow_id,),
            ).fetchone()
            if row is None:
                return None

            if float(row["replay_expires_at"]) <= current_time:
                connection.execute(
                    "DELETE FROM completed_logins WHERE flow_id = ?",
                    (flow_id,),
                )
                return None

        return self._row_to_completed_login(row)

    def purge_expired(self, now: float) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM pending_logins WHERE expires_at <= ?",
                (now,),
            )
            connection.execute(
                "DELETE FROM completed_logins WHERE replay_expires_at <= ?",
                (now,),
            )

    def claim_poll(
        self,
        *,
        flow_id: str,
        expected_version: int,
        claimed_version: int,
        next_poll_at: float,
        now: float,
    ) -> bool:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE pending_logins
                SET version = ?, next_poll_at = ?, updated_at = ?
                WHERE flow_id = ? AND version = ? AND expires_at > ? AND next_poll_at <= ?
                """,
                (
                    claimed_version,
                    next_poll_at,
                    now,
                    flow_id,
                    expected_version,
                    now,
                    now,
                ),
            )
            updated = cursor.rowcount == 1
        return updated

    def update_backoff(
        self,
        *,
        flow_id: str,
        expected_version: int,
        interval: int,
        next_poll_at: float,
        now: float,
    ) -> CopilotLoginTicket | None:
        with self._connection() as connection:
            cursor = connection.execute(
                """
                UPDATE pending_logins
                SET interval_seconds = ?, next_poll_at = ?, updated_at = ?
                WHERE flow_id = ? AND version = ?
                """,
                (
                    interval,
                    next_poll_at,
                    now,
                    flow_id,
                    expected_version,
                ),
            )
            if cursor.rowcount != 1:
                return None

            row = connection.execute(
                """
                SELECT
                    flow_id,
                    session_binding,
                    device_code,
                    user_code,
                    verification_uri,
                    verification_uri_complete,
                    interval_seconds,
                    expires_at,
                    next_poll_at,
                    version
                FROM pending_logins
                WHERE flow_id = ?
                """,
                (flow_id,),
            ).fetchone()

        if row is None:
            return None
        return self._row_to_ticket(row)

    def delete(self, flow_id: str) -> None:
        with self._connection() as connection:
            connection.execute(
                "DELETE FROM pending_logins WHERE flow_id = ?",
                (flow_id,),
            )

    def save_completed(
        self,
        completed: CopilotCompletedLogin,
    ) -> CopilotCompletedLogin:
        if not completed.flow_id:
            raise ValueError("Completed login results require a flow_id before they can be stored.")

        with self._connection() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO completed_logins (
                    flow_id,
                    session_binding,
                    credential_envelope,
                    credential_id,
                    copilot_token_expires_at,
                    replay_expires_at,
                    version
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    completed.flow_id,
                    completed.session_binding,
                    completed.credential_envelope,
                    completed.credential_id,
                    completed.copilot_token_expires_at,
                    completed.replay_expires_at,
                    completed.version,
                ),
            )
            connection.execute(
                "DELETE FROM pending_logins WHERE flow_id = ?",
                (completed.flow_id,),
            )

        return completed

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.db_path, timeout=5.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA busy_timeout = 5000")
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
                CREATE TABLE IF NOT EXISTS pending_logins (
                    flow_id TEXT PRIMARY KEY,
                    session_binding TEXT NOT NULL,
                    device_code TEXT NOT NULL,
                    user_code TEXT NOT NULL,
                    verification_uri TEXT NOT NULL,
                    verification_uri_complete TEXT,
                    interval_seconds INTEGER NOT NULL,
                    expires_at REAL NOT NULL,
                    next_poll_at REAL NOT NULL,
                    version INTEGER NOT NULL,
                    updated_at REAL NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_pending_logins_expires_at
                ON pending_logins (expires_at)
                """
            )
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS completed_logins (
                    flow_id TEXT PRIMARY KEY,
                    session_binding TEXT NOT NULL,
                    credential_envelope TEXT NOT NULL,
                    credential_id TEXT NOT NULL,
                    copilot_token_expires_at REAL NOT NULL,
                    replay_expires_at REAL NOT NULL,
                    version INTEGER NOT NULL
                )
                """
            )
            connection.execute(
                """
                CREATE INDEX IF NOT EXISTS idx_completed_logins_replay_expires_at
                ON completed_logins (replay_expires_at)
                """
            )

    @staticmethod
    def _row_to_ticket(row: sqlite3.Row) -> CopilotLoginTicket:
        return CopilotLoginTicket(
            session_binding=str(row["session_binding"]),
            device_code=str(row["device_code"]),
            user_code=str(row["user_code"]),
            verification_uri=str(row["verification_uri"]),
            verification_uri_complete=(
                None
                if row["verification_uri_complete"] is None
                else str(row["verification_uri_complete"])
            ),
            interval=max(int(row["interval_seconds"]), 1),
            expires_at=float(row["expires_at"]),
            next_poll_at=float(row["next_poll_at"]),
            flow_id=str(row["flow_id"]),
            version=max(int(row["version"]), 1),
        )

    @staticmethod
    def _row_to_completed_login(row: sqlite3.Row) -> CopilotCompletedLogin:
        return CopilotCompletedLogin(
            session_binding=str(row["session_binding"]),
            credential_envelope=str(row["credential_envelope"]),
            credential_id=str(row["credential_id"]),
            copilot_token_expires_at=float(row["copilot_token_expires_at"]),
            replay_expires_at=float(row["replay_expires_at"]),
            flow_id=str(row["flow_id"]),
            version=max(int(row["version"]), 1),
        )


class CopilotAuthService:
    def __init__(self) -> None:
        self.github_client_id = os.getenv("GITHUB_COPILOT_CLIENT_ID", DEFAULT_GITHUB_CLIENT_ID)
        self.github_device_code_url = os.getenv(
            "GITHUB_COPILOT_DEVICE_CODE_URL",
            DEFAULT_GITHUB_DEVICE_CODE_URL,
        )
        self.github_access_token_url = os.getenv(
            "GITHUB_COPILOT_ACCESS_TOKEN_URL",
            DEFAULT_GITHUB_ACCESS_TOKEN_URL,
        )
        self.github_api_key_url = os.getenv(
            "GITHUB_COPILOT_API_KEY_URL",
            DEFAULT_GITHUB_API_KEY_URL,
        )
        self.github_user_url = os.getenv(
            "GITHUB_COPILOT_USER_URL",
            DEFAULT_GITHUB_USER_URL,
        )
        configured_usage_urls = os.getenv("GITHUB_COPILOT_USAGE_URLS", "")
        self.github_usage_urls = tuple(
            url.strip()
            for url in configured_usage_urls.split(",")
            if url.strip()
        ) or DEFAULT_GITHUB_USAGE_URLS
        self.session_cookie_name = os.getenv(
            "COPILOT_SESSION_COOKIE_NAME",
            "copilot_client_binding",
        )
        self.session_cookie_secure = (
            os.getenv("COPILOT_SESSION_COOKIE_SECURE", "false").strip().lower() == "true"
        )
        self.session_cookie_max_age = int(
            os.getenv("COPILOT_SESSION_COOKIE_MAX_AGE", str(60 * 60 * 24 * 30))
        )
        self.refresh_skew_seconds = int(
            os.getenv("COPILOT_TOKEN_REFRESH_SKEW_SECONDS", "60")
        )
        self.completed_login_ttl_seconds = max(
            int(
                os.getenv(
                    "COPILOT_COMPLETED_LOGIN_TTL_SECONDS",
                    str(DEFAULT_COMPLETED_LOGIN_TTL_SECONDS),
                )
            ),
            1,
        )
        self.http_timeout = httpx.Timeout(20.0, connect=10.0)
        pending_login_db_path = os.getenv("COPILOT_PENDING_LOGIN_DB_PATH")
        self.pending_login_db_path = (
            Path(pending_login_db_path)
            if pending_login_db_path
            else DEFAULT_PENDING_LOGIN_DB_PATH
        )

        secret = os.getenv("COPILOT_ENVELOPE_SECRET")
        if secret:
            self.is_ephemeral_secret = False
            derived_secret = secret
        else:
            derived_secret = secrets.token_urlsafe(48)
            self.is_ephemeral_secret = True
            LOGGER.warning(
                "COPILOT_ENVELOPE_SECRET is not set; using an ephemeral runtime secret. "
                "Stored Copilot envelopes will stop working after restart."
            )

        self._fernet = Fernet(self._derive_fernet_key(derived_secret))
        self._binding_key = self._derive_binding_key(derived_secret)
        self._pending_login_store = PendingLoginStore(self.pending_login_db_path)

    def get_or_create_session_secret(self, request: Request) -> tuple[str, bool]:
        session_secret = request.cookies.get(self.session_cookie_name)
        if session_secret:
            return session_secret, False
        return secrets.token_urlsafe(32), True

    def apply_session_cookie(self, response: Response, session_secret: str) -> None:
        response.set_cookie(
            key=self.session_cookie_name,
            value=session_secret,
            max_age=self.session_cookie_max_age,
            httponly=True,
            samesite="strict",
            secure=self.session_cookie_secure,
            path="/",
        )

    def rotate_session_secret(self, response: Response) -> str:
        session_secret = secrets.token_urlsafe(32)
        self.apply_session_cookie(response, session_secret)
        return session_secret

    def get_conversation_scope(self, session_secret: str) -> str:
        digest = hmac.new(
            self._binding_key,
            f"copilot-conversation-scope:{session_secret}".encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

    def get_anonymous_history_scope(self, browser_scope_id: str) -> str:
        return browser_scope_id

    def get_user_history_scope(self, github_user_id: str) -> str:
        return f"user:{github_user_id}"

    def get_authenticated_history_owner(
        self,
        session: CopilotCredentialSession,
    ) -> HistoryOwnerContext:
        if not session.github_user_id:
            raise CopilotAuthError(
                status_code=503,
                message="GitHub 계정 정보를 확인하지 못했습니다. 다시 로그인하세요.",
                code="copilot_account_invalid",
            )
        return HistoryOwnerContext(
            scope_id=self.get_user_history_scope(session.github_user_id),
            authenticated=True,
            github_user_id=session.github_user_id,
            github_login=session.github_login,
        )

    def inspect_envelope(self, envelope: str | None, session_secret: str) -> dict[str, Any]:
        if not envelope:
            return self._unauthenticated_status_payload()

        try:
            session = self._decrypt_envelope(envelope, session_secret)
        except CopilotAuthError as exc:
            payload = self._unauthenticated_status_payload()
            payload.update(
                {
                    "code": exc.code,
                    "message": exc.message,
                    "shouldClearEnvelope": True,
                }
            )
            return payload

        return self._authenticated_status_payload(session)

    async def get_status_payload(
        self,
        envelope: str | None,
        session_secret: str,
    ) -> tuple[dict[str, Any], str | None]:
        payload = self.inspect_envelope(envelope, session_secret)
        if not payload.get("authenticated"):
            return payload, None

        try:
            session, refreshed_envelope = await self.resolve_session(str(envelope), session_secret)
        except CopilotAuthError as exc:
            fallback = self._unauthenticated_status_payload()
            fallback.update(
                {
                    "code": exc.code,
                    "message": exc.message,
                    "shouldClearEnvelope": True,
                }
            )
            return fallback, None

        payload["usage"] = await self.fetch_usage_snapshot(session.github_access_token)
        return payload, refreshed_envelope

    async def resolve_history_scope(
        self,
        envelope: str | None,
        session_secret: str,
        browser_scope_id: str,
    ) -> tuple[HistoryOwnerContext, str | None]:
        anonymous_scope = self.get_anonymous_history_scope(browser_scope_id)
        if not envelope:
            return HistoryOwnerContext(scope_id=anonymous_scope, authenticated=False), None

        try:
            session, refreshed_envelope = await self.resolve_session(envelope, session_secret)
        except CopilotAuthError:
            return HistoryOwnerContext(scope_id=anonymous_scope, authenticated=False), None

        if not session.github_user_id:
            return HistoryOwnerContext(scope_id=anonymous_scope, authenticated=False), refreshed_envelope

        return (
            HistoryOwnerContext(
                scope_id=self.get_user_history_scope(session.github_user_id),
                authenticated=True,
                github_user_id=session.github_user_id,
                github_login=session.github_login,
            ),
            refreshed_envelope,
        )

    async def start_login(self, session_secret: str) -> dict[str, Any]:
        self._pending_login_store.purge_expired(time.time())
        device_payload = await self._request_device_code()
        interval = max(int(device_payload.get("interval", 5)), 1)
        expires_in = max(int(device_payload.get("expires_in", 900)), 60)
        now = time.time()
        pending = CopilotLoginTicket(
            session_binding=self._session_binding(session_secret),
            device_code=device_payload["device_code"],
            user_code=device_payload["user_code"],
            verification_uri=device_payload["verification_uri"],
            verification_uri_complete=device_payload.get("verification_uri_complete"),
            interval=interval,
            expires_at=now + expires_in,
            next_poll_at=now + interval,
            flow_id=secrets.token_urlsafe(18),
            version=1,
        )
        self._pending_login_store.save(pending, now=now)

        payload = self._login_ticket_response(pending, now=now)
        payload.update(
            {
                "userCode": pending.user_code,
                "verificationUri": pending.verification_uri,
                "verificationUriComplete": pending.verification_uri_complete,
            }
        )
        return payload

    async def poll_login(self, login_id: str, session_secret: str) -> dict[str, Any]:
        handle = self._decrypt_login_handle(login_id)
        current_binding = self._session_binding(session_secret)
        now = time.time()

        completed = self._pending_login_store.get_completed(handle.flow_id, now=now)
        if completed is not None:
            self._assert_login_session_binding(current_binding, handle, completed)
            return await self._completed_login_response(handle, completed, session_secret)

        try:
            pending = self._require_pending_login(handle.flow_id)
        except CopilotAuthError:
            completed = self._pending_login_store.get_completed(handle.flow_id, now=time.time())
            if completed is not None:
                self._assert_login_session_binding(current_binding, handle, completed)
                return await self._completed_login_response(handle, completed, session_secret)
            raise

        self._assert_login_session_binding(current_binding, handle, pending)

        if pending.expires_at <= now:
            self._pending_login_store.delete(pending.flow_id)
            raise CopilotAuthError(
                status_code=410,
                message="GitHub 로그인 코드가 만료되었습니다. 다시 로그인하세요.",
                code="copilot_login_expired",
            )

        if handle.version != pending.version:
            return self._pending_login_response(handle, pending, now=now)

        if pending.next_poll_at > now:
            payload = self._login_ticket_response(pending, now=now)
            payload["status"] = "pending"
            return payload

        claimed_version = pending.version + 1
        claimed_next_poll_at = now + pending.interval
        if not self._pending_login_store.claim_poll(
            flow_id=pending.flow_id,
            expected_version=pending.version,
            claimed_version=claimed_version,
            next_poll_at=claimed_next_poll_at,
            now=now,
        ):
            completed = self._pending_login_store.get_completed(handle.flow_id, now=time.time())
            if completed is not None:
                self._assert_login_session_binding(current_binding, handle, completed)
                return await self._completed_login_response(handle, completed, session_secret)
            latest_pending = self._require_pending_login(handle.flow_id)
            self._assert_login_session_binding(current_binding, handle, latest_pending)
            return self._pending_login_response(handle, latest_pending, now=time.time())

        claimed_pending = CopilotLoginTicket(
            session_binding=pending.session_binding,
            device_code=pending.device_code,
            user_code=pending.user_code,
            verification_uri=pending.verification_uri,
            verification_uri_complete=pending.verification_uri_complete,
            interval=pending.interval,
            expires_at=pending.expires_at,
            next_poll_at=claimed_next_poll_at,
            flow_id=pending.flow_id,
            version=claimed_version,
        )

        token_result = await self._poll_access_token_once(claimed_pending.device_code)
        status = token_result["status"]
        if status == "pending":
            interval_delta = int(token_result.get("interval_delta", 0))
            next_interval = max(claimed_pending.interval + interval_delta, 1)
            refreshed_now = time.time()
            updated_pending = self._pending_login_store.update_backoff(
                flow_id=claimed_pending.flow_id,
                expected_version=claimed_pending.version,
                interval=next_interval,
                next_poll_at=refreshed_now + next_interval,
                now=refreshed_now,
            )
            if updated_pending is None:
                updated_pending = self._require_pending_login(claimed_pending.flow_id)
                self._assert_login_session_binding(current_binding, handle, updated_pending)
            payload = self._login_ticket_response(updated_pending, now=refreshed_now)
            payload["status"] = "pending"
            return payload

        if status == "denied":
            self._pending_login_store.delete(claimed_pending.flow_id)
            raise CopilotAuthError(
                status_code=401,
                message="GitHub 인증이 거부되었습니다.",
                code="copilot_login_denied",
            )

        if status == "expired":
            self._pending_login_store.delete(claimed_pending.flow_id)
            raise CopilotAuthError(
                status_code=410,
                message="GitHub 로그인 코드가 만료되었습니다. 다시 로그인하세요.",
                code="copilot_login_expired",
            )

        access_token = token_result.get("access_token")
        if not isinstance(access_token, str) or not access_token:
            raise CopilotAuthError(
                status_code=401,
                message="GitHub 액세스 토큰을 가져오지 못했습니다.",
                code="copilot_access_token_missing",
            )

        session = await self._build_credential_session(access_token)
        envelope = self._encrypt_session(session, session_secret)
        completed_now = time.time()
        completed = CopilotCompletedLogin(
            session_binding=claimed_pending.session_binding,
            credential_envelope=envelope,
            credential_id=session.credential_id,
            copilot_token_expires_at=session.copilot_api_expires_at,
            replay_expires_at=min(
                completed_now + self.completed_login_ttl_seconds,
                session.copilot_api_expires_at,
            ),
            flow_id=claimed_pending.flow_id,
            version=claimed_pending.version,
        )
        self._pending_login_store.save_completed(completed)
        return await self._completed_login_response(handle, completed, session_secret, session=session)

    async def resolve_session(
        self,
        envelope: str,
        session_secret: str,
    ) -> tuple[CopilotCredentialSession, str | None]:
        session = self._decrypt_envelope(envelope, session_secret)
        refreshed_envelope: str | None = None
        if self._needs_refresh(session):
            refreshed_session = await self._build_credential_session(session.github_access_token)
            refreshed_session.issued_at = session.issued_at
            session = refreshed_session
            refreshed_envelope = self._encrypt_session(session, session_secret)
        elif not session.github_user_id or not session.github_login:
            session = await self._with_github_user_identity(session)
            refreshed_envelope = self._encrypt_session(session, session_secret)

        return session, refreshed_envelope

    async def _with_github_user_identity(
        self,
        session: CopilotCredentialSession,
    ) -> CopilotCredentialSession:
        if session.github_user_id and session.github_login:
            return session

        github_user_id, github_login = await self._request_github_user_profile(session.github_access_token)
        return CopilotCredentialSession(
            github_access_token=session.github_access_token,
            copilot_api_token=session.copilot_api_token,
            copilot_api_expires_at=session.copilot_api_expires_at,
            copilot_api_base=session.copilot_api_base,
            issued_at=session.issued_at,
            updated_at=time.time(),
            credential_id=session.credential_id,
            github_user_id=github_user_id,
            github_login=github_login,
        )

    async def _request_device_code(self) -> dict[str, Any]:
        payload = {
            "client_id": self.github_client_id,
            "scope": "read:user",
        }

        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.post(
                    self.github_device_code_url,
                    headers=self._github_headers(content_type=GITHUB_FORM_CONTENT_TYPE),
                    data=payload,
                )
        except httpx.RequestError as exc:
            LOGGER.warning("GitHub device flow start failed: %s", exc)
            raise CopilotAuthError(
                status_code=503,
                message="GitHub 로그인 요청을 시작하지 못했습니다. 잠시 후 다시 시도하세요.",
                code="copilot_device_flow_unreachable",
            ) from exc

        response_payload = self._json_payload(response, "GitHub 로그인 코드를 가져오지 못했습니다.")
        required_fields = {"device_code", "user_code", "verification_uri"}
        if not required_fields.issubset(response_payload):
            raise CopilotAuthError(
                status_code=502,
                message="GitHub 로그인 응답에 필요한 코드 정보가 없습니다.",
                code="copilot_device_flow_invalid_response",
            )
        return response_payload

    async def _poll_access_token_once(self, device_code: str) -> dict[str, Any]:
        payload = {
            "client_id": self.github_client_id,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        }

        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.post(
                    self.github_access_token_url,
                    headers=self._github_headers(content_type=GITHUB_FORM_CONTENT_TYPE),
                    data=payload,
                )
        except httpx.RequestError as exc:
            LOGGER.warning("GitHub access token poll failed: %s", exc)
            raise CopilotAuthError(
                status_code=503,
                message="GitHub 로그인 상태를 확인하지 못했습니다. 잠시 후 다시 시도하세요.",
                code="copilot_access_token_unreachable",
            ) from exc

        response_payload = self._json_payload(response, "GitHub 액세스 토큰 응답을 해석하지 못했습니다.")
        access_token = response_payload.get("access_token")
        if isinstance(access_token, str) and access_token:
            return {"status": "complete", "access_token": access_token}

        error_code = response_payload.get("error")
        if error_code == "authorization_pending":
            return {"status": "pending", "interval_delta": 0}
        if error_code == "slow_down":
            return {"status": "pending", "interval_delta": 5}
        if error_code == "expired_token":
            return {"status": "expired"}
        if error_code == "access_denied":
            return {"status": "denied"}

        raise CopilotAuthError(
            status_code=401,
            message="GitHub 인증을 완료하지 못했습니다. 다시 시도하세요.",
            code="copilot_access_token_failed",
        )

    async def _build_credential_session(self, access_token: str) -> CopilotCredentialSession:
        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.get(
                    self.github_api_key_url,
                    headers=self._github_headers(access_token),
                )
        except httpx.RequestError as exc:
            LOGGER.warning("GitHub Copilot token fetch failed: %s", exc)
            raise CopilotAuthError(
                status_code=503,
                message="GitHub Copilot 토큰을 가져오지 못했습니다. 잠시 후 다시 시도하세요.",
                code="copilot_api_token_unreachable",
            ) from exc

        payload = self._json_payload(response, "GitHub Copilot 토큰 응답을 해석하지 못했습니다.")
        token = payload.get("token")
        expires_at = payload.get("expires_at")
        api_base = payload.get("endpoints", {}).get("api") or DEFAULT_GITHUB_COPILOT_API_BASE

        if not isinstance(token, str) or not token:
            raise CopilotAuthError(
                status_code=401,
                message="GitHub Copilot 토큰 응답에 token 이 없습니다.",
                code="copilot_api_token_missing",
            )

        try:
            expires_at_value = float(expires_at)
        except (TypeError, ValueError) as exc:
            raise CopilotAuthError(
                status_code=502,
                message="GitHub Copilot 토큰 만료 시각을 해석하지 못했습니다.",
                code="copilot_api_token_invalid_expiry",
            ) from exc

        github_user_id, github_login = await self._request_github_user_profile(access_token)
        now = time.time()
        return CopilotCredentialSession(
            github_access_token=access_token,
            copilot_api_token=token,
            copilot_api_expires_at=expires_at_value,
            copilot_api_base=str(api_base).rstrip("/"),
            issued_at=now,
            updated_at=now,
            credential_id=self._credential_id(access_token),
            github_user_id=github_user_id,
            github_login=github_login,
        )

    async def _request_github_user_profile(self, access_token: str) -> tuple[str, str]:
        try:
            async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                response = await client.get(
                    self.github_user_url,
                    headers=self._github_headers(access_token),
                )
        except httpx.RequestError as exc:
            LOGGER.warning("GitHub user profile fetch failed: %s", exc)
            raise CopilotAuthError(
                status_code=503,
                message="GitHub 계정 정보를 확인하지 못했습니다. 잠시 후 다시 시도하세요.",
                code="copilot_account_unreachable",
            ) from exc

        payload = self._json_payload(response, "GitHub 계정 정보를 확인하지 못했습니다.")
        github_user_id = payload.get("id")
        github_login = payload.get("login")
        if github_user_id is None or not isinstance(github_login, str) or not github_login.strip():
            raise CopilotAuthError(
                status_code=502,
                message="GitHub 계정 정보를 확인하지 못했습니다. 다시 로그인하세요.",
                code="copilot_account_invalid",
            )
        return str(github_user_id), github_login.strip()

    async def fetch_usage_snapshot(self, access_token: str) -> dict[str, Any]:
        last_shape_source: str | None = None

        for url in self.github_usage_urls:
            source = self._usage_source(url)
            try:
                async with httpx.AsyncClient(timeout=self.http_timeout) as client:
                    response = await client.get(
                        url,
                        headers=self._github_headers(access_token),
                    )
            except httpx.RequestError as exc:
                LOGGER.warning("GitHub usage request failed for %s: %s", source or url, exc)
                continue

            payload = self._safe_json_payload(response)
            if payload is None:
                LOGGER.warning("GitHub usage response was not valid JSON for %s", source or url)
                continue

            if response.status_code in {401, 403}:
                return self._usage_snapshot_unavailable(
                    reason="copilot_usage_auth_failed",
                    source=source,
                )

            if response.status_code == 404:
                continue

            if response.status_code >= 400:
                LOGGER.warning(
                    "GitHub usage request returned %s for %s",
                    response.status_code,
                    source or url,
                )
                continue

            snapshot = self._normalize_usage_snapshot(payload, source=source)
            if snapshot["status"] != "unavailable":
                return snapshot

            if snapshot["reason"] == "copilot_usage_shape_unrecognized":
                last_shape_source = source

        if last_shape_source is not None:
            return self._usage_snapshot_unavailable(
                reason="copilot_usage_shape_unrecognized",
                source=last_shape_source,
            )

        return self._usage_snapshot_unavailable(reason="copilot_usage_unavailable")

    def _login_ticket_response(
        self,
        ticket: CopilotLoginTicket,
        *,
        now: float | None = None,
    ) -> dict[str, Any]:
        current_time = time.time() if now is None else now
        return {
            "loginId": self._encrypt_login_handle(self._login_handle_from_ticket(ticket)),
            "interval": ticket.interval,
            "expiresAt": ticket.expires_at,
            "nextPollAt": ticket.next_poll_at,
            "retryAfter": max(ticket.next_poll_at - current_time, 0.0),
        }

    def _encrypt_login_ticket(self, ticket: CopilotLoginTicket) -> str:
        stored_ticket = ticket
        if not stored_ticket.flow_id:
            stored_ticket = CopilotLoginTicket(
                session_binding=ticket.session_binding,
                device_code=ticket.device_code,
                user_code=ticket.user_code,
                verification_uri=ticket.verification_uri,
                verification_uri_complete=ticket.verification_uri_complete,
                interval=ticket.interval,
                expires_at=ticket.expires_at,
                next_poll_at=ticket.next_poll_at,
                flow_id=secrets.token_urlsafe(18),
                version=max(int(ticket.version), 1),
            )

        self._pending_login_store.save(stored_ticket)
        return self._encrypt_login_handle(self._login_handle_from_ticket(stored_ticket))

    def _encrypt_login_handle(self, handle: CopilotLoginHandle) -> str:
        payload = {
            "version": 1,
            "kind": "login_handle",
            "binding": handle.session_binding,
            "flow_id": handle.flow_id,
            "flow_version": handle.version,
        }
        serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(serialized).decode("utf-8")

    def _decrypt_login_ticket(self, login_id: str, session_secret: str) -> CopilotLoginTicket:
        handle = self._decrypt_login_handle(login_id)
        pending = self._require_pending_login(handle.flow_id)
        current_binding = self._session_binding(session_secret)
        self._assert_login_session_binding(current_binding, handle, pending)
        if pending.expires_at <= time.time():
            self._pending_login_store.delete(pending.flow_id)
            raise CopilotAuthError(
                status_code=410,
                message="GitHub 로그인 코드가 만료되었습니다. 다시 로그인하세요.",
                code="copilot_login_expired",
            )
        if handle.version > pending.version:
            raise CopilotAuthError(
                status_code=401,
                message="로그인 세션을 확인할 수 없습니다. 다시 로그인하세요.",
                code="copilot_login_invalid",
            )
        return pending

    def _decrypt_login_handle(self, login_id: str) -> CopilotLoginHandle:
        try:
            decrypted = self._fernet.decrypt(login_id.encode("utf-8"))
        except (InvalidToken, ValueError) as exc:
            raise CopilotAuthError(
                status_code=401,
                message="로그인 세션을 확인할 수 없습니다. 다시 로그인하세요.",
                code="copilot_login_invalid",
            ) from exc

        try:
            payload = json.loads(decrypted.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CopilotAuthError(
                status_code=401,
                message="로그인 세션 형식이 올바르지 않습니다. 다시 로그인하세요.",
                code="copilot_login_invalid",
            ) from exc

        if not isinstance(payload, dict) or payload.get("kind") != "login_handle":
            raise CopilotAuthError(
                status_code=401,
                message="로그인 세션을 확인할 수 없습니다. 다시 로그인하세요.",
                code="copilot_login_invalid",
            )

        try:
            handle = CopilotLoginHandle(
                flow_id=str(payload["flow_id"]),
                session_binding=str(payload["binding"]),
                version=max(int(payload["flow_version"]), 1),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CopilotAuthError(
                status_code=401,
                message="로그인 세션에 필요한 값이 없습니다. 다시 로그인하세요.",
                code="copilot_login_invalid",
            ) from exc

        return handle

    def _require_pending_login(self, flow_id: str) -> CopilotLoginTicket:
        pending = self._pending_login_store.get(flow_id)
        if pending is None:
            raise CopilotAuthError(
                status_code=401,
                message="로그인 세션을 확인할 수 없습니다. 다시 로그인하세요.",
                code="copilot_login_invalid",
            )
        return pending

    def _assert_login_session_binding(
        self,
        current_binding: str,
        handle: CopilotLoginHandle,
        pending: CopilotLoginTicket | CopilotCompletedLogin,
    ) -> None:
        handle_matches_current = handle.session_binding == current_binding
        pending_matches_current = pending.session_binding == current_binding
        handle_matches_pending = handle.session_binding == pending.session_binding
        if not handle_matches_current or not pending_matches_current:
            raise CopilotAuthError(
                status_code=403,
                message="현재 브라우저 세션과 맞지 않는 로그인 요청입니다. 다시 시도하세요.",
                code="copilot_login_session_mismatch",
                log_context={
                    "flow_id_prefix": self._debug_prefix(handle.flow_id),
                    "login_state": "completed" if isinstance(pending, CopilotCompletedLogin) else "pending",
                    "handle_version": handle.version,
                    "pending_version": pending.version,
                    "handle_matches_current": handle_matches_current,
                    "pending_matches_current": pending_matches_current,
                    "handle_matches_pending": handle_matches_pending,
                    "reason": self._session_mismatch_reason(
                        handle_matches_current=handle_matches_current,
                        pending_matches_current=pending_matches_current,
                        handle_matches_pending=handle_matches_pending,
                    ),
                    "ephemeral_secret": self.is_ephemeral_secret,
                },
            )

    def _pending_login_response(
        self,
        handle: CopilotLoginHandle,
        pending: CopilotLoginTicket,
        *,
        now: float,
    ) -> dict[str, Any]:
        if handle.version > pending.version:
            raise CopilotAuthError(
                status_code=401,
                message="로그인 세션을 확인할 수 없습니다. 다시 로그인하세요.",
                code="copilot_login_invalid",
            )

        payload = self._login_ticket_response(pending, now=now)
        payload["status"] = "pending"
        return payload

    async def _completed_login_response(
        self,
        handle: CopilotLoginHandle,
        completed: CopilotCompletedLogin,
        session_secret: str,
        session: CopilotCredentialSession | None = None,
    ) -> dict[str, Any]:
        if handle.version > completed.version:
            raise CopilotAuthError(
                status_code=401,
                message="로그인 세션을 확인할 수 없습니다. 다시 로그인하세요.",
                code="copilot_login_invalid",
            )

        usage_snapshot = self._usage_snapshot_unavailable(reason="copilot_usage_unavailable")
        resolved_session = session
        if resolved_session is None:
            try:
                resolved_session = self._decrypt_envelope(
                    completed.credential_envelope,
                    session_secret,
                )
            except CopilotAuthError:
                usage_snapshot = self._usage_snapshot_unavailable(
                    reason="copilot_usage_unavailable",
                )
        if resolved_session is not None:
            usage_snapshot = await self.fetch_usage_snapshot(resolved_session.github_access_token)

        return {
            "status": "complete",
            "credentialEnvelope": completed.credential_envelope,
            "credentialId": completed.credential_id,
            "copilotTokenExpiresAt": completed.copilot_token_expires_at,
            "usage": usage_snapshot,
        }

    def _authenticated_status_payload(self, session: CopilotCredentialSession) -> dict[str, Any]:
        return {
            "authenticated": True,
            "credentialId": session.credential_id,
            "copilotTokenExpiresAt": session.copilot_api_expires_at,
            "needsRefresh": self._needs_refresh(session),
            "shouldClearEnvelope": False,
            "ephemeralSecret": self.is_ephemeral_secret,
            "usage": self._usage_snapshot_unavailable(reason="copilot_usage_pending"),
        }

    def _unauthenticated_status_payload(self) -> dict[str, Any]:
        return {
            "authenticated": False,
            "shouldClearEnvelope": False,
            "ephemeralSecret": self.is_ephemeral_secret,
            "usage": self._usage_snapshot_unavailable(reason="not_authenticated"),
        }

    def _usage_snapshot_unavailable(
        self,
        *,
        reason: str,
        detail: str | None = None,
        source: str | None = None,
    ) -> dict[str, Any]:
        return {
            "status": "unavailable",
            "reason": reason,
            "detail": self._usage_detail(reason) if detail is None else detail,
            "source": source,
            "fetchedAt": time.time(),
            "chatMessages": self._empty_usage_metric(),
            "premiumRequests": self._empty_usage_metric(),
        }

    def _empty_usage_metric(self) -> dict[str, Any]:
        return {
            "remaining": None,
            "used": None,
            "total": None,
            "plan": None,
            "unlimited": False,
            "status": "missing",
        }

    def _normalize_usage_snapshot(self, payload: dict[str, Any], *, source: str | None) -> dict[str, Any]:
        if self._is_free_limited_usage_payload(payload):
            return self._normalize_free_limited_usage_snapshot(payload, source=source)
        if self._is_quota_snapshots_usage_payload(payload):
            return self._normalize_quota_snapshots_usage_snapshot(payload, source=source)

        chat_payload = self._build_usage_metric_payload(
            payload,
            aliases=(("chat", "messages"), ("chat",)),
        )
        premium_payload = self._build_usage_metric_payload(
            payload,
            aliases=(("premium", "requests"), ("premium", "interactions"), ("premium",)),
        )
        premium_payload = self._normalize_premium_requests_metric(
            premium_payload,
            source=source,
        )

        if chat_payload["status"] == "missing" and premium_payload["status"] == "missing":
            return self._usage_snapshot_unavailable(
                reason="copilot_usage_shape_unrecognized",
                source=source,
            )

        detail = None
        status = "ok"
        reason = "copilot_usage_ok"
        if chat_payload["status"] == "missing" or premium_payload["status"] == "missing":
            status = "partial"
            reason = "copilot_usage_partial"
            detail = self._usage_detail(reason)

        return {
            "status": status,
            "reason": reason,
            "detail": detail,
            "source": source,
            "fetchedAt": time.time(),
            "accessTypeSku": self._normalize_text(payload.get("access_type_sku")),
            "chatMessages": chat_payload,
            "premiumRequests": premium_payload,
        }

    def _is_free_limited_usage_payload(self, payload: dict[str, Any]) -> bool:
        access_type_sku = payload.get("access_type_sku")
        limited_user_quotas = payload.get("limited_user_quotas")
        monthly_quotas = payload.get("monthly_quotas")
        return (
            isinstance(access_type_sku, str)
            and access_type_sku.strip().lower() == "free_limited_copilot"
            and isinstance(limited_user_quotas, dict)
            and isinstance(monthly_quotas, dict)
        )

    def _normalize_free_limited_usage_snapshot(
        self,
        payload: dict[str, Any],
        *,
        source: str | None,
    ) -> dict[str, Any]:
        limited_user_quotas = payload.get("limited_user_quotas")
        monthly_quotas = payload.get("monthly_quotas")
        if not isinstance(limited_user_quotas, dict) or not isinstance(monthly_quotas, dict):
            return self._usage_snapshot_unavailable(
                reason="copilot_usage_shape_unrecognized",
                source=source,
            )

        remaining_chat = self._normalize_usage_basis_value(
            self._coerce_usage_number(limited_user_quotas.get("chat"))
        )
        total_chat = self._normalize_usage_total_value(
            self._coerce_usage_number(monthly_quotas.get("chat"))
        )
        used_chat: int | None = None
        if isinstance(remaining_chat, int) and isinstance(total_chat, int) and total_chat >= remaining_chat:
            used_chat = total_chat - remaining_chat

        chat_payload = {
            "remaining": remaining_chat,
            "used": used_chat,
            "total": total_chat,
            "plan": self._normalize_text(payload.get("copilot_plan"))
            or self._normalize_text(payload.get("access_type_sku")),
            "unlimited": False,
            "status": "available"
            if remaining_chat is not None or total_chat is not None or used_chat is not None
            else "missing",
        }
        premium_payload = self._empty_usage_metric()

        status = "partial"
        reason = "copilot_usage_partial"
        if chat_payload["status"] == "missing":
            status = "unavailable"
            reason = "copilot_usage_shape_unrecognized"

        return {
            "status": status,
            "reason": reason,
            "detail": self._usage_detail(reason),
            "source": source,
            "fetchedAt": time.time(),
            "accessTypeSku": self._normalize_text(payload.get("access_type_sku")),
            "chatMessages": chat_payload,
            "premiumRequests": premium_payload,
        }

    def _is_quota_snapshots_usage_payload(self, payload: dict[str, Any]) -> bool:
        quota_snapshots = payload.get("quota_snapshots")
        return isinstance(quota_snapshots, dict) and any(
            key in quota_snapshots for key in ("chat", "premium_interactions", "completions")
        )

    def _normalize_quota_snapshots_usage_snapshot(
        self,
        payload: dict[str, Any],
        *,
        source: str | None,
    ) -> dict[str, Any]:
        quota_snapshots = payload.get("quota_snapshots")
        if not isinstance(quota_snapshots, dict):
            return self._usage_snapshot_unavailable(
                reason="copilot_usage_shape_unrecognized",
                source=source,
            )

        plan = self._normalize_text(payload.get("copilot_plan")) or self._normalize_text(
            payload.get("access_type_sku")
        )
        chat_payload = self._normalize_quota_snapshot_metric(
            quota_snapshots.get("chat"),
            plan=plan,
        )
        premium_payload = self._normalize_quota_snapshot_metric(
            quota_snapshots.get("premium_interactions"),
            plan=plan,
        )

        if chat_payload["status"] == "missing" and premium_payload["status"] == "missing":
            return self._usage_snapshot_unavailable(
                reason="copilot_usage_shape_unrecognized",
                source=source,
            )

        detail = None
        status = "ok"
        reason = "copilot_usage_ok"
        if chat_payload["status"] == "missing" or premium_payload["status"] == "missing":
            status = "partial"
            reason = "copilot_usage_partial"
            detail = self._usage_detail(reason)

        return {
            "status": status,
            "reason": reason,
            "detail": detail,
            "source": source,
            "fetchedAt": time.time(),
            "accessTypeSku": self._normalize_text(payload.get("access_type_sku")),
            "chatMessages": chat_payload,
            "premiumRequests": premium_payload,
        }

    def _normalize_quota_snapshot_metric(
        self,
        raw_metric: Any,
        *,
        plan: str | None,
    ) -> dict[str, Any]:
        if not isinstance(raw_metric, dict):
            return self._empty_usage_metric()

        unlimited = bool(raw_metric.get("unlimited"))
        remaining = self._normalize_usage_basis_value(
            self._coerce_usage_number(raw_metric.get("remaining"))
        )
        total = self._normalize_usage_total_value(
            self._coerce_usage_number(raw_metric.get("entitlement"))
        )
        used: int | None = None
        if isinstance(remaining, int) and isinstance(total, int) and total >= remaining:
            used = total - remaining

        has_quota_basis = remaining is not None or total is not None or used is not None
        if unlimited:
            remaining = None
            total = None
            used = None

        return {
            "remaining": remaining,
            "used": used,
            "total": total,
            "plan": plan,
            "unlimited": unlimited,
            "status": "available" if unlimited or has_quota_basis else "missing",
        }

    def _build_usage_metric_payload(
        self,
        payload: dict[str, Any],
        *,
        aliases: tuple[tuple[str, ...], ...],
    ) -> dict[str, Any]:
        remaining = self._extract_usage_value(
            payload,
            aliases=aliases,
            preferred_tokens={"remaining", "left", "available", "balance"},
            disallowed_tokens={"used", "spent", "consumed", "total", "limit", "maximum", "max", "plan", "tier", "subscription"},
        )
        used = self._extract_usage_value(
            payload,
            aliases=aliases,
            preferred_tokens={"used", "spent", "consumed"},
            disallowed_tokens={"remaining", "left", "available", "balance", "total", "limit", "maximum", "max", "plan", "tier", "subscription"},
        )
        total = self._extract_usage_value(
            payload,
            aliases=aliases,
            preferred_tokens={"total", "limit", "maximum", "max", "included", "quota", "allowance"},
            disallowed_tokens={"remaining", "left", "available", "balance", "used", "spent", "consumed", "plan", "tier", "subscription"},
        )
        plan = self._extract_usage_text(
            payload,
            aliases=aliases,
            preferred_tokens={"plan", "tier", "subscription", "product"},
            disallowed_tokens={"remaining", "left", "available", "balance", "used", "spent", "consumed", "total", "limit", "maximum", "max", "reset", "renewal", "expiry", "expires", "timestamp", "date"},
        )
        remaining = self._normalize_usage_basis_value(remaining)
        used = self._normalize_usage_basis_value(used)
        total = self._normalize_usage_total_value(total)
        has_quota_basis = remaining is not None or used is not None or total is not None
        return {
            "remaining": remaining,
            "used": used,
            "total": total,
            "plan": plan,
            "status": "available" if has_quota_basis else "missing",
        }

    def _normalize_premium_requests_metric(
        self,
        metric: dict[str, Any],
        *,
        source: str | None,
    ) -> dict[str, Any]:
        remaining = metric.get("remaining")
        used = metric.get("used")
        total = metric.get("total")
        if isinstance(total, int) and total <= 0:
            total = None
            metric = {
                **metric,
                "total": None,
            }
        if not isinstance(remaining, int) or used is not None or total is not None:
            return metric

        if remaining < 0 or remaining > 100:
            return metric

        return {
            **metric,
            "used": 100 - remaining,
            "total": 100,
        }

    def _normalize_usage_basis_value(self, value: int | None) -> int | None:
        if not isinstance(value, int):
            return None
        if value < 0:
            return None
        return value

    def _normalize_usage_total_value(self, value: int | None) -> int | None:
        if not isinstance(value, int):
            return None
        if value <= 0:
            return None
        return value

    def _usage_detail(self, reason: str) -> str | None:
        return USAGE_DETAIL_MESSAGES.get(reason)

    def _usage_source(self, url: str) -> str | None:
        normalized = url.rstrip("/")
        if normalized in USAGE_SOURCE_MAP:
            return USAGE_SOURCE_MAP[normalized]
        if normalized.endswith("/copilot_internal/v2/usage"):
            return "copilot_usage_api"
        if normalized.endswith("/copilot_internal/user"):
            return "copilot_user_api"
        return "copilot_usage_other"

    def _extract_usage_value(
        self,
        payload: dict[str, Any],
        *,
        aliases: tuple[tuple[str, ...], ...],
        preferred_tokens: set[str],
        disallowed_tokens: set[str],
    ) -> int | None:
        best_score = 0
        best_value: int | None = None

        for path, numeric_value in self._iter_numeric_paths(payload):
            score = self._score_usage_path(
                path,
                aliases=aliases,
                preferred_tokens=preferred_tokens,
                disallowed_tokens=disallowed_tokens,
            )
            if score <= best_score:
                continue
            best_score = score
            best_value = numeric_value

        if best_score < 12:
            return None
        return best_value

    def _extract_usage_text(
        self,
        payload: dict[str, Any],
        *,
        aliases: tuple[tuple[str, ...], ...],
        preferred_tokens: set[str],
        disallowed_tokens: set[str],
    ) -> str | None:
        best_score = 0
        best_value: str | None = None

        for path, text_value in self._iter_text_paths(payload):
            score = self._score_usage_path(
                path,
                aliases=aliases,
                preferred_tokens=preferred_tokens,
                disallowed_tokens=disallowed_tokens,
            )
            if score <= best_score:
                continue
            best_score = score
            best_value = text_value

        if best_score < 12:
            return None
        return best_value

    def _iter_numeric_paths(
        self,
        value: Any,
        path: tuple[str, ...] = (),
    ) -> Iterator[tuple[tuple[str, ...], int]]:
        if isinstance(value, dict):
            for key, nested_value in value.items():
                yield from self._iter_numeric_paths(nested_value, path + (str(key),))
            return

        if isinstance(value, list):
            for index, nested_value in enumerate(value):
                yield from self._iter_numeric_paths(nested_value, path + (str(index),))
            return

        coerced = self._coerce_usage_number(value)
        if coerced is not None:
            yield path, coerced

    def _iter_text_paths(
        self,
        value: Any,
        path: tuple[str, ...] = (),
    ) -> Iterator[tuple[tuple[str, ...], str]]:
        if isinstance(value, dict):
            for key, nested_value in value.items():
                yield from self._iter_text_paths(nested_value, path + (str(key),))
            return

        if isinstance(value, list):
            for index, nested_value in enumerate(value):
                yield from self._iter_text_paths(nested_value, path + (str(index),))
            return

        coerced = self._coerce_usage_text(value)
        if coerced is not None:
            yield path, coerced

    def _score_usage_path(
        self,
        path: tuple[str, ...],
        *,
        aliases: tuple[tuple[str, ...], ...],
        preferred_tokens: set[str],
        disallowed_tokens: set[str],
    ) -> int:
        tokens = self._usage_path_tokens(path)
        token_set = set(tokens)
        if not token_set:
            return 0

        score = 0
        if any(all(alias in token_set for alias in alias_group) for alias_group in aliases):
            score += 8

        if any(token in token_set for token in preferred_tokens):
            score += 6

        if any(token in token_set for token in {"usage", "quota", "quotas", "snapshot", "allowance"}):
            score += 2

        if any(token in token_set for token in disallowed_tokens):
            score -= 8

        return score

    def _usage_path_tokens(self, path: tuple[str, ...]) -> list[str]:
        tokens: list[str] = []
        for component in path:
            normalized = re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", component)
            tokens.extend(
                token
                for token in re.split(r"[^a-z0-9]+", normalized.lower())
                if token and not token.isdigit()
            )
        return tokens

    def _coerce_usage_number(self, value: Any) -> int | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return value
        if isinstance(value, float):
            if value < 0:
                return None
            return int(value)
        if isinstance(value, str):
            text = value.strip()
            if not text or not re.fullmatch(r"\d+(?:\.0+)?", text):
                return None
            return int(float(text))
        return None

    def _coerce_usage_text(self, value: Any) -> str | None:
        if isinstance(value, bool):
            return None
        if isinstance(value, int):
            return str(value)
        if isinstance(value, float):
            if value < 0:
                return None
            return str(int(value)) if value.is_integer() else str(value)
        if isinstance(value, str):
            text = value.strip()
            return text or None
        return None

    def _safe_json_payload(self, response: httpx.Response) -> dict[str, Any] | None:
        try:
            payload = response.json()
        except ValueError:
            return None
        if not isinstance(payload, dict):
            return None
        return payload

    @staticmethod
    def _login_handle_from_ticket(ticket: CopilotLoginTicket) -> CopilotLoginHandle:
        if not ticket.flow_id:
            raise ValueError("Pending login tickets require a flow_id before a handle can be created.")
        return CopilotLoginHandle(
            flow_id=ticket.flow_id,
            session_binding=ticket.session_binding,
            version=ticket.version,
        )

    def _encrypt_session(self, session: CopilotCredentialSession, session_secret: str) -> str:
        payload = {
            "version": 1,
            "binding": self._session_binding(session_secret),
            "github_access_token": session.github_access_token,
            "copilot_api_token": session.copilot_api_token,
            "copilot_api_expires_at": session.copilot_api_expires_at,
            "copilot_api_base": session.copilot_api_base,
            "issued_at": session.issued_at,
            "updated_at": session.updated_at,
            "credential_id": session.credential_id,
            "github_user_id": session.github_user_id,
            "github_login": session.github_login,
        }
        serialized = json.dumps(payload, separators=(",", ":")).encode("utf-8")
        return self._fernet.encrypt(serialized).decode("utf-8")

    def _decrypt_envelope(self, envelope: str, session_secret: str) -> CopilotCredentialSession:
        try:
            decrypted = self._fernet.decrypt(envelope.encode("utf-8"))
        except (InvalidToken, ValueError) as exc:
            raise CopilotAuthError(
                status_code=401,
                message="저장된 GitHub Copilot 자격 정보를 복호화할 수 없습니다. 다시 로그인하세요.",
                code="copilot_credential_invalid",
            ) from exc

        try:
            payload = json.loads(decrypted.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise CopilotAuthError(
                status_code=401,
                message="저장된 GitHub Copilot 자격 정보 형식이 올바르지 않습니다. 다시 로그인하세요.",
                code="copilot_credential_invalid",
            ) from exc

        if payload.get("binding") != self._session_binding(session_secret):
            raise CopilotAuthError(
                status_code=401,
                message="저장된 GitHub Copilot 자격 정보가 현재 브라우저 세션과 맞지 않습니다. 다시 로그인하세요.",
                code="copilot_credential_binding_mismatch",
            )

        try:
            return CopilotCredentialSession(
                github_access_token=str(payload["github_access_token"]),
                copilot_api_token=str(payload["copilot_api_token"]),
                copilot_api_expires_at=float(payload["copilot_api_expires_at"]),
                copilot_api_base=str(payload["copilot_api_base"]).rstrip("/"),
                issued_at=float(payload.get("issued_at", time.time())),
                updated_at=float(payload.get("updated_at", time.time())),
                credential_id=str(payload.get("credential_id") or self._credential_id(str(payload["github_access_token"]))),
                github_user_id=self._normalize_text(payload.get("github_user_id")),
                github_login=self._normalize_text(payload.get("github_login")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise CopilotAuthError(
                status_code=401,
                message="저장된 GitHub Copilot 자격 정보에 필요한 값이 없습니다. 다시 로그인하세요.",
                code="copilot_credential_invalid",
            ) from exc

    def _needs_refresh(self, session: CopilotCredentialSession) -> bool:
        return session.copilot_api_expires_at <= (time.time() + self.refresh_skew_seconds)

    def _session_binding(self, session_secret: str) -> str:
        digest = hmac.new(
            self._binding_key,
            session_secret.encode("utf-8"),
            hashlib.sha256,
        ).digest()
        return base64.urlsafe_b64encode(digest).decode("utf-8").rstrip("=")

    @staticmethod
    def _normalize_text(value: Any) -> str | None:
        if not isinstance(value, str):
            return None
        normalized = value.strip()
        return normalized or None

    @staticmethod
    def _debug_prefix(value: str, width: int = 8) -> str:
        if not value:
            return ""
        return value[:width]

    @staticmethod
    def _session_mismatch_reason(
        *,
        handle_matches_current: bool,
        pending_matches_current: bool,
        handle_matches_pending: bool,
    ) -> str:
        if handle_matches_pending and not handle_matches_current and not pending_matches_current:
            return "browser_session_changed_or_missing_cookie"
        if pending_matches_current and not handle_matches_current:
            return "stale_or_cross_session_login_id"
        if handle_matches_current and not pending_matches_current:
            return "pending_store_binding_drift"
        return "inconsistent_login_binding_state"

    def _credential_id(self, access_token: str) -> str:
        return hashlib.sha256(access_token.encode("utf-8")).hexdigest()[:12]

    def _github_headers(
        self,
        access_token: str | None = None,
        *,
        content_type: str | None = None,
    ) -> dict[str, str]:
        headers = {
            "accept": "application/json",
            "editor-version": "vscode/1.85.1",
            "editor-plugin-version": "copilot/1.155.0",
            "user-agent": "GithubCopilot/1.155.0",
            "accept-encoding": "gzip,deflate,br",
        }
        if content_type:
            headers["content-type"] = content_type
        if access_token:
            headers["authorization"] = f"token {access_token}"
        return headers

    def _json_payload(self, response: httpx.Response, fallback_message: str) -> dict[str, Any]:
        try:
            payload = response.json()
        except ValueError as exc:
            raise CopilotAuthError(
                status_code=502,
                message=fallback_message,
                code="copilot_json_invalid",
            ) from exc

        if response.status_code >= 400:
            upstream_hint = self._infer_upstream_failure_hint(
                upstream_url=str(response.request.url),
                status_code=response.status_code,
                payload=payload,
                github_sso_header=response.headers.get("x-github-sso"),
            )
            raise CopilotAuthError(
                status_code=response.status_code,
                message=self._upstream_user_message(
                    payload=payload,
                    fallback_message=fallback_message,
                    upstream_hint=upstream_hint,
                ),
                code="copilot_upstream_error",
                log_context=self._upstream_error_log_context(response, payload),
            )

        if isinstance(payload, dict):
            return payload

        raise CopilotAuthError(
            status_code=502,
            message=fallback_message,
            code="copilot_json_invalid",
        )

    @staticmethod
    def _derive_binding_key(secret: str) -> bytes:
        return hashlib.sha256(f"copilot-binding:{secret}".encode("utf-8")).digest()

    def _upstream_error_log_context(
        self,
        response: httpx.Response,
        payload: Any,
    ) -> dict[str, Any]:
        upstream_url = str(response.request.url)
        sanitized_payload = self._sanitize_upstream_payload(payload)
        body_excerpt = self._upstream_body_excerpt(sanitized_payload)
        return {
            "upstream_stage": self._upstream_stage(upstream_url),
            "upstream_url": upstream_url,
            "upstream_status_code": response.status_code,
            "upstream_request_method": response.request.method,
            "upstream_github_request_id": response.headers.get("x-github-request-id"),
            "upstream_github_sso": response.headers.get("x-github-sso"),
            "upstream_www_authenticate": response.headers.get("www-authenticate"),
            "upstream_content_type": response.headers.get("content-type"),
            "upstream_hint": self._infer_upstream_failure_hint(
                upstream_url=upstream_url,
                status_code=response.status_code,
                payload=sanitized_payload,
                github_sso_header=response.headers.get("x-github-sso"),
            ),
            "upstream_body_excerpt": body_excerpt,
        }

    @staticmethod
    def _upstream_stage(upstream_url: str) -> str:
        if upstream_url.endswith("/login/device/code"):
            return "device_code"
        if upstream_url.endswith("/login/oauth/access_token"):
            return "device_access_token"
        if upstream_url.endswith("/user"):
            return "github_user_profile"
        if "/copilot_internal/v2/token" in upstream_url:
            return "copilot_token"
        if "/copilot_internal/" in upstream_url:
            return "copilot_api"
        return "unknown"

    def _infer_upstream_failure_hint(
        self,
        *,
        upstream_url: str,
        status_code: int,
        payload: Any,
        github_sso_header: str | None,
    ) -> str | None:
        if status_code not in {401, 403}:
            return None

        stage = self._upstream_stage(upstream_url)
        body_text = self._normalized_upstream_text(payload)
        sso_text = (github_sso_header or "").strip().lower()

        if any(term in sso_text for term in ("required", "partial")) or any(
            term in body_text for term in ("sso", "saml", "reauthorize")
        ):
            return "sso_or_managed_account_authorization_required"

        if any(term in body_text for term in ("enterprise", "organization", "org ", "policy", "business")):
            return "organization_or_enterprise_policy_may_be_blocking_copilot"

        if any(
            term in body_text
            for term in (
                "not enabled",
                "not entitled",
                "entitlement",
                "no access",
                "not allowed",
                "copilot",
                "license",
                "seat",
            )
        ):
            return "account_may_not_have_copilot_entitlement"

        if stage == "copilot_token":
            return "copilot_token_request_forbidden_check_entitlement_or_org_policy"
        if stage == "device_access_token":
            return "device_flow_token_exchange_forbidden_or_client_restricted"
        return "github_upstream_auth_or_policy_rejected_request"

    def _upstream_user_message(
        self,
        *,
        payload: Any,
        fallback_message: str,
        upstream_hint: str | None,
    ) -> str:
        if upstream_hint == "account_may_not_have_copilot_entitlement":
            login = self._extract_upstream_login(payload)
            if login:
                return (
                    f"현재 로그인한 GitHub 계정({login})에 GitHub Copilot 권한이 없습니다. "
                    "Copilot이 활성화된 계정으로 다시 로그인하거나, 해당 계정에 Copilot 권한이 있는지 확인하세요."
                )
            return (
                "현재 로그인한 GitHub 계정에 GitHub Copilot 권한이 없습니다. "
                "Copilot이 활성화된 계정으로 다시 로그인하거나, 해당 계정에 Copilot 권한이 있는지 확인하세요."
            )

        if upstream_hint == "organization_or_enterprise_policy_may_be_blocking_copilot":
            return (
                "현재 로그인한 GitHub 계정은 조직 또는 엔터프라이즈 정책 때문에 Copilot 사용이 차단된 것 같습니다. "
                "조직 관리자에게 Copilot seat 또는 정책 설정을 확인하세요."
            )

        if upstream_hint == "sso_or_managed_account_authorization_required":
            return (
                "현재 로그인한 GitHub 계정은 조직 SSO 또는 관리형 계정 승인 절차가 필요합니다. "
                "GitHub에서 조직 접근 권한을 승인한 뒤 다시 시도하세요."
            )

        return fallback_message

    def _extract_upstream_login(self, payload: Any) -> str | None:
        payload_text = self._upstream_body_excerpt(payload, max_length=1000)
        match = re.search(r"logged in as ([A-Za-z0-9-]+)", payload_text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
        return None

    def _normalized_upstream_text(self, payload: Any) -> str:
        excerpt = self._upstream_body_excerpt(payload)
        return excerpt.lower()

    def _upstream_body_excerpt(self, payload: Any, max_length: int = 280) -> str:
        if isinstance(payload, (dict, list)):
            text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        else:
            text = str(payload)
        compact = re.sub(r"\s+", " ", text).strip()
        if len(compact) <= max_length:
            return compact
        return f"{compact[:max_length - 3]}..."

    def _sanitize_upstream_payload(self, payload: Any) -> Any:
        if isinstance(payload, dict):
            sanitized: dict[str, Any] = {}
            for key, value in payload.items():
                lowered = str(key).lower()
                if any(secret_key in lowered for secret_key in ("token", "secret", "password")):
                    sanitized[str(key)] = "[redacted]"
                elif lowered == "access_token":
                    sanitized[str(key)] = "[redacted]"
                else:
                    sanitized[str(key)] = self._sanitize_upstream_payload(value)
            return sanitized
        if isinstance(payload, list):
            return [self._sanitize_upstream_payload(item) for item in payload]
        if isinstance(payload, str):
            return self._redact_upstream_text(payload)
        return payload

    @staticmethod
    def _redact_upstream_text(value: str) -> str:
        redacted = re.sub(r"(?i)(access[_-]?token|refresh[_-]?token|token|secret|password)\s*[:=]\s*[^,\s]+", r"\1=[redacted]", value)
        return re.sub(r"\bgh[opusr]_[A-Za-z0-9_]+\b", "[redacted]", redacted)

    @staticmethod
    def _derive_fernet_key(secret: str) -> bytes:
        digest = hashlib.sha256(f"copilot-envelope:{secret}".encode("utf-8")).digest()
        return base64.urlsafe_b64encode(digest)