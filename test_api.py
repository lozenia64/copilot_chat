from __future__ import annotations

import importlib
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from services.copilot_auth import CopilotAuthService, CopilotCredentialSession, CopilotLoginTicket
from services.copilot_chat import CopilotChatService


def make_credential_session(
    *,
    github_access_token: str = "github-access-token",
    copilot_api_token: str = "copilot-api-token",
    expires_at: float | None = None,
    credential_id: str = "cred-123",
) -> CopilotCredentialSession:
    now = time.time()
    return CopilotCredentialSession(
        github_access_token=github_access_token,
        copilot_api_token=copilot_api_token,
        copilot_api_expires_at=expires_at if expires_at is not None else now + 3600,
        copilot_api_base="https://api.githubcopilot.com",
        issued_at=now,
        updated_at=now,
        credential_id=credential_id,
    )


def make_usage_snapshot(
    *,
    status: str = "ok",
    chat_remaining: int | None = 42,
    premium_remaining: int | None = 7,
    detail: str | None = None,
    reason: str | None = None,
    source: str | None = "copilot_usage_api",
) -> dict[str, object]:
    return {
        "status": status,
        "reason": reason or ("copilot_usage_ok" if status == "ok" else "copilot_usage_partial"),
        "detail": detail,
        "source": source,
        "fetchedAt": time.time(),
        "chatMessages": {
            "remaining": chat_remaining,
            "status": "available" if chat_remaining is not None else "missing",
        },
        "premiumRequests": {
            "remaining": premium_remaining,
            "status": "available" if premium_remaining is not None else "missing",
        },
    }


class DeviceFlowEncodingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_patcher = patch.dict(
            os.environ,
            {
                "COPILOT_ENVELOPE_SECRET": "test-envelope-secret",
                "COPILOT_PENDING_LOGIN_DB_PATH": str(
                    Path(self.temp_dir.name) / "pending-login-state.sqlite3"
                ),
            },
            clear=False,
        )
        self.env_patcher.start()
        self.service = CopilotAuthService()

    async def asyncTearDown(self) -> None:
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def _issue_login_ticket(
        self,
        session_secret: str,
        *,
        interval: int = 5,
        expires_at: float | None = None,
        next_poll_at: float | None = None,
        device_code: str = "device-code",
    ) -> str:
        now = time.time()
        ticket = CopilotLoginTicket(
            session_binding=self.service._session_binding(session_secret),
            device_code=device_code,
            user_code="user-code",
            verification_uri="https://github.com/login/device",
            verification_uri_complete=None,
            interval=interval,
            expires_at=expires_at if expires_at is not None else now + 300,
            next_poll_at=next_poll_at if next_poll_at is not None else now - 1,
        )
        return self.service._encrypt_login_ticket(ticket)

    async def test_start_login_uses_form_urlencoded_device_flow_request(self) -> None:
        requests: list[dict[str, object]] = []

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

            async def post(self, url, headers=None, json=None, data=None):
                requests.append(
                    {
                        "url": url,
                        "headers": headers,
                        "json": json,
                        "data": data,
                    }
                )
                return httpx.Response(
                    200,
                    json={
                        "device_code": "device-code",
                        "user_code": "user-code",
                        "verification_uri": "https://github.com/login/device",
                        "interval": 5,
                        "expires_in": 900,
                    },
                    request=httpx.Request("POST", url),
                )

        with patch("services.copilot_auth.httpx.AsyncClient", FakeAsyncClient):
            payload = await self.service.start_login("browser-session-secret")

        self.assertIn("loginId", payload)
        login_ticket = self.service._decrypt_login_ticket(payload["loginId"], "browser-session-secret")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["json"], None)
        self.assertEqual(
            requests[0]["headers"]["content-type"],
            "application/x-www-form-urlencoded",
        )
        self.assertEqual(
            requests[0]["data"],
            {"client_id": self.service.github_client_id, "scope": "read:user"},
        )
        self.assertEqual(login_ticket.device_code, "device-code")
        self.assertEqual(login_ticket.user_code, "user-code")
        self.assertEqual(login_ticket.interval, 5)
        self.assertAlmostEqual(payload["nextPollAt"], login_ticket.next_poll_at, delta=1)
        self.assertGreater(payload["retryAfter"], 0)

    async def test_poll_login_uses_form_urlencoded_access_token_request(self) -> None:
        requests: list[dict[str, object]] = []
        session_secret = "browser-session-secret"
        login_id = self._issue_login_ticket(session_secret)
        usage_snapshot = make_usage_snapshot(chat_remaining=12, premium_remaining=2)

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

            async def post(self, url, headers=None, json=None, data=None):
                requests.append(
                    {
                        "url": url,
                        "headers": headers,
                        "json": json,
                        "data": data,
                    }
                )
                return httpx.Response(
                    200,
                    json={"access_token": "github-access-token"},
                    request=httpx.Request("POST", url),
                )

        with patch("services.copilot_auth.httpx.AsyncClient", FakeAsyncClient):
            with patch.object(
                self.service,
                "_build_credential_session",
                AsyncMock(return_value=make_credential_session()),
            ):
                with patch.object(
                    self.service,
                    "fetch_usage_snapshot",
                    AsyncMock(return_value=usage_snapshot),
                ):
                    payload = await self.service.poll_login(login_id, session_secret)

        self.assertEqual(payload["status"], "complete")
        self.assertEqual(len(requests), 1)
        self.assertEqual(requests[0]["json"], None)
        self.assertEqual(
            requests[0]["headers"]["content-type"],
            "application/x-www-form-urlencoded",
        )
        self.assertEqual(
            requests[0]["data"],
            {
                "client_id": self.service.github_client_id,
                "device_code": "device-code",
                "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
            },
        )

    async def test_poll_login_enforces_next_poll_at_without_upstream_call(self) -> None:
        session_secret = "browser-session-secret"
        next_poll_at = time.time() + 30
        login_id = self._issue_login_ticket(
            session_secret,
            next_poll_at=next_poll_at,
        )

        with patch.object(self.service, "_poll_access_token_once", AsyncMock()) as poll_access_token:
            payload = await self.service.poll_login(login_id, session_secret)

        poll_access_token.assert_not_called()
        self.assertEqual(payload["status"], "pending")
        self.assertIn("loginId", payload)
        self.assertGreater(payload["retryAfter"], 0)

        refreshed_ticket = self.service._decrypt_login_ticket(payload["loginId"], session_secret)
        self.assertEqual(refreshed_ticket.device_code, "device-code")
        self.assertAlmostEqual(refreshed_ticket.next_poll_at, next_poll_at, delta=1)

    async def test_poll_login_returns_refreshed_login_handle_on_pending(self) -> None:
        session_secret = "browser-session-secret"
        login_id = self._issue_login_ticket(session_secret, interval=5)

        with patch.object(
            self.service,
            "_poll_access_token_once",
            AsyncMock(return_value={"status": "pending", "interval_delta": 5}),
        ) as poll_access_token:
            payload = await self.service.poll_login(login_id, session_secret)

        poll_access_token.assert_awaited_once_with("device-code")
        self.assertEqual(payload["status"], "pending")
        self.assertIn("loginId", payload)
        self.assertNotEqual(payload["loginId"], login_id)

        refreshed_ticket = self.service._decrypt_login_ticket(payload["loginId"], session_secret)
        self.assertEqual(refreshed_ticket.interval, 10)
        self.assertGreater(refreshed_ticket.next_poll_at, time.time())

    async def test_poll_login_works_across_service_instances_with_shared_pending_store(self) -> None:
        session_secret = "browser-session-secret"
        start_service = CopilotAuthService()
        poll_service = CopilotAuthService()

        with patch.object(
            start_service,
            "_request_device_code",
            AsyncMock(
                return_value={
                    "device_code": "device-code",
                    "user_code": "user-code",
                    "verification_uri": "https://github.com/login/device",
                    "verification_uri_complete": "https://github.com/login/device?user_code=user-code",
                    "interval": 5,
                    "expires_in": 900,
                }
            ),
        ):
            payload = await start_service.start_login(session_secret)

        with patch.object(poll_service, "_poll_access_token_once", AsyncMock()) as poll_access_token:
            poll_payload = await poll_service.poll_login(payload["loginId"], session_secret)

        poll_access_token.assert_not_called()
        self.assertEqual(poll_payload["status"], "pending")
        self.assertIn("loginId", poll_payload)
        self.assertGreater(poll_payload["retryAfter"], 0)

        refreshed_ticket = poll_service._decrypt_login_ticket(poll_payload["loginId"], session_secret)
        self.assertEqual(refreshed_ticket.device_code, "device-code")
        self.assertEqual(refreshed_ticket.user_code, "user-code")

    async def test_poll_login_replays_completed_result_for_current_and_older_handles(self) -> None:
        session_secret = "browser-session-secret"
        original_login_id = self._issue_login_ticket(session_secret, interval=5)
        usage_snapshot = make_usage_snapshot(chat_remaining=8, premium_remaining=1)

        with patch.object(
            self.service,
            "_poll_access_token_once",
            AsyncMock(return_value={"status": "pending", "interval_delta": 5}),
        ):
            pending_payload = await self.service.poll_login(original_login_id, session_secret)

        refreshed_login_id = pending_payload["loginId"]
        refreshed_ticket = self.service._decrypt_login_ticket(refreshed_login_id, session_secret)
        refreshed_ticket.next_poll_at = time.time() - 1
        self.service._pending_login_store.save(refreshed_ticket, now=time.time())

        complete_service = CopilotAuthService()
        replay_service = CopilotAuthService()
        expected_session = make_credential_session(
            github_access_token="github-access-token",
            copilot_api_token="copilot-api-token",
            credential_id="cred-complete",
        )

        with patch.object(
            complete_service,
            "_poll_access_token_once",
            AsyncMock(return_value={"status": "complete", "access_token": "github-access-token"}),
        ) as complete_poll:
            with patch.object(
                complete_service,
                "_build_credential_session",
                AsyncMock(return_value=expected_session),
            ):
                with patch.object(
                    complete_service,
                    "fetch_usage_snapshot",
                    AsyncMock(return_value=usage_snapshot),
                ):
                    complete_payload = await complete_service.poll_login(refreshed_login_id, session_secret)

        complete_poll.assert_awaited_once_with("device-code")
        self.assertEqual(complete_payload["status"], "complete")

        with patch.object(replay_service, "_poll_access_token_once", AsyncMock()) as replay_poll:
            with patch.object(
                replay_service,
                "fetch_usage_snapshot",
                AsyncMock(return_value=usage_snapshot),
            ):
                replay_current_payload = await replay_service.poll_login(refreshed_login_id, session_secret)
                replay_older_payload = await replay_service.poll_login(original_login_id, session_secret)

        replay_poll.assert_not_called()
        self.assertEqual(replay_current_payload, complete_payload)
        self.assertEqual(replay_older_payload, complete_payload)

    async def test_fetch_usage_snapshot_falls_back_to_secondary_endpoint(self) -> None:
        service = self.service
        service.github_usage_urls = (
            "https://api.github.com/copilot_internal/v2/usage",
            "https://api.github.com/copilot_internal/user",
        )

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

            async def get(self, url, headers=None):
                if url.endswith("/v2/usage"):
                    return httpx.Response(
                        404,
                        json={"message": "Not Found"},
                        request=httpx.Request("GET", url),
                    )

                return httpx.Response(
                    200,
                    json={
                        "usage": {
                            "remaining_chat_messages": 18,
                            "premium_requests_remaining": 3,
                        }
                    },
                    request=httpx.Request("GET", url),
                )

        with patch("services.copilot_auth.httpx.AsyncClient", FakeAsyncClient):
            snapshot = await service.fetch_usage_snapshot("github-access-token")

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["chatMessages"]["remaining"], 18)
        self.assertEqual(snapshot["premiumRequests"]["remaining"], 3)
        self.assertEqual(snapshot["source"], "copilot_user_api")

    async def test_fetch_usage_snapshot_marks_partial_when_only_one_metric_exists(self) -> None:
        service = self.service
        service.github_usage_urls = ("https://api.github.com/copilot_internal/v2/usage",)

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

            async def get(self, url, headers=None):
                return httpx.Response(
                    200,
                    json={
                        "quotas": {
                            "chat": {"remaining": 11},
                        }
                    },
                    request=httpx.Request("GET", url),
                )

        with patch("services.copilot_auth.httpx.AsyncClient", FakeAsyncClient):
            snapshot = await service.fetch_usage_snapshot("github-access-token")

        self.assertEqual(snapshot["status"], "partial")
        self.assertEqual(snapshot["chatMessages"]["remaining"], 11)
        self.assertEqual(snapshot["premiumRequests"]["remaining"], None)
        self.assertEqual(snapshot["premiumRequests"]["status"], "missing")
        self.assertEqual(snapshot["detail"], "GitHub Copilot 사용량 정보 일부만 확인되었습니다.")
        self.assertEqual(snapshot["source"], "copilot_usage_api")

    async def test_fetch_usage_snapshot_request_error_hides_url_and_exception_text(self) -> None:
        service = self.service
        service.github_usage_urls = ("https://api.github.com/copilot_internal/v2/usage",)

        class FakeAsyncClient:
            def __init__(self, *args, **kwargs) -> None:
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

            async def get(self, url, headers=None):
                raise httpx.ConnectError("dial tcp 127.0.0.1:443: boom", request=httpx.Request("GET", url))

        with patch("services.copilot_auth.httpx.AsyncClient", FakeAsyncClient):
            snapshot = await service.fetch_usage_snapshot("github-access-token")

        self.assertEqual(snapshot["status"], "unavailable")
        self.assertEqual(snapshot["reason"], "copilot_usage_unavailable")
        self.assertEqual(snapshot["detail"], "GitHub Copilot 사용량 정보를 지금 확인할 수 없습니다.")
        self.assertEqual(snapshot["source"], None)
        self.assertNotIn("127.0.0.1", snapshot["detail"])
        self.assertNotIn("api.github.com", snapshot["detail"])


class ChatStreamingContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_start_failure_returns_sse_error_and_done(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )

        class FakeRequest:
            async def is_disconnected(self) -> bool:
                return False

        with patch(
            "services.copilot_chat.litellm.acompletion",
            AsyncMock(side_effect=RuntimeError("startup failed")),
        ):
            chunks = [
                chunk
                async for chunk in service.stream_chat_completion(
                    request=FakeRequest(),
                    model="gpt-5.4",
                    messages=[{"role": "user", "content": "hello"}],
                    session=make_credential_session(),
                )
            ]

        payload = b"".join(chunks).decode("utf-8")
        self.assertIn('data: {"code": "copilot_chat_stream_failed", "message": "채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요."}', payload)
        self.assertNotIn("startup failed", payload)
        self.assertTrue(payload.rstrip().endswith("data: [DONE]"))

    async def test_stream_midstream_error_chunk_returns_sanitized_sse_error_and_done(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )

        class FakeRequest:
            async def is_disconnected(self) -> bool:
                return False

        class FakeStream:
            def __init__(self, chunks: list[object]) -> None:
                self._chunks = chunks
                self._index = 0
                self.closed = False

            def __aiter__(self):
                return self

            async def __anext__(self):
                if self._index >= len(self._chunks):
                    raise StopAsyncIteration
                chunk = self._chunks[self._index]
                self._index += 1
                return chunk

            async def aclose(self) -> None:
                self.closed = True

        fake_stream = FakeStream(
            [
                {"choices": [{"delta": {"content": "hello"}}]},
                {
                    "error": {
                        "message": "provider exploded",
                        "type": "upstream_error",
                    },
                    "detail": "https://api.githubcopilot.com/internal failure",
                },
            ]
        )

        with patch(
            "services.copilot_chat.litellm.acompletion",
            AsyncMock(return_value=fake_stream),
        ):
            chunks = [
                chunk
                async for chunk in service.stream_chat_completion(
                    request=FakeRequest(),
                    model="gpt-5.4",
                    messages=[{"role": "user", "content": "hello"}],
                    session=make_credential_session(),
                )
            ]

        payload = b"".join(chunks).decode("utf-8")
        self.assertIn('data: {"choices": [{"delta": {"content": "hello"}}]}', payload)
        self.assertIn('data: {"code": "copilot_chat_stream_failed", "message": "채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요."}', payload)
        self.assertNotIn("provider exploded", payload)
        self.assertNotIn("api.githubcopilot.com", payload)
        self.assertTrue(fake_stream.closed)
        self.assertTrue(payload.rstrip().endswith("data: [DONE]"))


class FrontendUsageContractTests(unittest.TestCase):
    def test_usage_summary_ignores_snapshot_detail_in_ui_code(self) -> None:
        source = (Path(__file__).resolve().parent / "static" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("snapshot.detail", source)
        self.assertIn("USAGE_SUMMARY_REASON_MESSAGES", source)


class CopilotApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_patcher = patch.dict(
            os.environ,
            {
                "COPILOT_ENVELOPE_SECRET": "test-envelope-secret",
                "COPILOT_PENDING_LOGIN_DB_PATH": str(
                    Path(self.temp_dir.name) / "pending-login-state.sqlite3"
                ),
            },
            clear=False,
        )
        self.env_patcher.start()
        self.main = importlib.import_module("main")
        self.main = importlib.reload(self.main)
        self.client = TestClient(self.main.app)

    def tearDown(self) -> None:
        self.client.close()
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def _establish_browser_session(self) -> str:
        response = self.client.post(
            "/api/copilot/status",
            json={"credentialEnvelope": None},
        )
        self.assertEqual(response.status_code, 200)
        session_secret = self.client.cookies.get(self.main.auth_service.session_cookie_name)
        self.assertIsNotNone(session_secret)
        return session_secret

    def _issue_envelope(
        self,
        session_secret: str,
        *,
        expires_at: float | None = None,
        credential_id: str = "cred-123",
    ) -> str:
        session = make_credential_session(expires_at=expires_at, credential_id=credential_id)
        return self.main.auth_service._encrypt_session(session, session_secret)

    def test_status_without_envelope_returns_unauthenticated_state(self) -> None:
        response = self.client.post(
            "/api/copilot/status",
            json={"credentialEnvelope": None},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(response.headers.get("set-cookie"))
        self.assertEqual(
            response.json(),
            {
                "authenticated": False,
                "shouldClearEnvelope": False,
                "ephemeralSecret": self.main.auth_service.is_ephemeral_secret,
                "usage": {
                    "status": "unavailable",
                    "reason": "not_authenticated",
                    "detail": None,
                    "source": None,
                    "fetchedAt": response.json()["usage"]["fetchedAt"],
                    "chatMessages": {
                        "remaining": None,
                        "status": "missing",
                    },
                    "premiumRequests": {
                        "remaining": None,
                        "status": "missing",
                    },
                },
            },
        )

    def test_invalid_envelope_requests_client_clear(self) -> None:
        self._establish_browser_session()

        response = self.client.post(
            "/api/copilot/status",
            json={"credentialEnvelope": "not-a-valid-envelope"},
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertFalse(payload["authenticated"])
        self.assertTrue(payload["shouldClearEnvelope"])
        self.assertEqual(payload["code"], "copilot_credential_invalid")
        self.assertEqual(payload["message"], "저장된 GitHub Copilot 자격 정보를 복호화할 수 없습니다. 다시 로그인하세요.")

    def test_status_with_valid_envelope_includes_usage_snapshot(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)
        usage_snapshot = make_usage_snapshot(chat_remaining=15, premium_remaining=2)

        with patch.object(
            self.main.auth_service,
            "fetch_usage_snapshot",
            AsyncMock(return_value=usage_snapshot),
        ) as fetch_usage_snapshot:
            response = self.client.post(
                "/api/copilot/status",
                json={"credentialEnvelope": envelope},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        fetch_usage_snapshot.assert_awaited_once()
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["credentialId"], "cred-123")
        self.assertEqual(payload["usage"]["chatMessages"]["remaining"], 15)
        self.assertEqual(payload["usage"]["premiumRequests"]["remaining"], 2)

    def test_status_usage_failure_keeps_authenticated_state(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)
        unavailable_snapshot = make_usage_snapshot(
            status="unavailable",
            chat_remaining=None,
            premium_remaining=None,
            detail="GitHub Copilot 사용량 정보를 지금 확인할 수 없습니다.",
            reason="copilot_usage_unavailable",
            source=None,
        )

        with patch.object(
            self.main.auth_service,
            "fetch_usage_snapshot",
            AsyncMock(return_value=unavailable_snapshot),
        ):
            response = self.client.post(
                "/api/copilot/status",
                json={"credentialEnvelope": envelope},
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertTrue(payload["authenticated"])
        self.assertEqual(payload["usage"]["status"], "unavailable")
        self.assertEqual(payload["usage"]["detail"], "GitHub Copilot 사용량 정보를 지금 확인할 수 없습니다.")

    def test_chat_requires_login_when_envelope_is_missing(self) -> None:
        response = self.client.post(
            "/api/chat",
            json={
                "model": "gpt-5.4",
                "messages": [{"role": "user", "content": "hello"}],
                "credentialEnvelope": None,
            },
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "copilot_login_required")
        self.assertEqual(
            response.json()["message"],
            "이 브라우저의 GitHub Copilot 자격 정보가 없습니다. 먼저 로그인하세요.",
        )

    def test_login_poll_with_invalid_handle_returns_safe_error_contract(self) -> None:
        response = self.client.post(
            "/api/copilot/login/poll",
            json={"loginId": "not-a-valid-login-handle"},
        )

        self.assertEqual(response.status_code, 401)
        payload = response.json()
        self.assertEqual(payload["code"], "copilot_login_invalid")
        self.assertEqual(payload["message"], "로그인 세션을 확인할 수 없습니다. 다시 로그인하세요.")

    def test_chat_validation_returns_safe_error_contract(self) -> None:
        response = self.client.post(
            "/api/chat",
            json={
                "model": "not-allowed-model",
                "messages": [{"role": "user", "content": "hello"}],
                "credentialEnvelope": "opaque-value",
            },
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["code"], "chat_model_not_allowed")
        self.assertEqual(payload["message"], "선택한 모델은 사용할 수 없습니다. 목록에서 다시 선택하세요.")

    def test_malformed_request_bodies_return_validation_error_contract(self) -> None:
        cases = [
            ("/api/copilot/status", []),
            ("/api/copilot/login/poll", {}),
            (
                "/api/chat",
                {
                    "model": "gpt-5.4",
                    "messages": "not-a-list",
                    "credentialEnvelope": "opaque-value",
                },
            ),
        ]

        for path, payload in cases:
            with self.subTest(path=path):
                response = self.client.post(path, json=payload)

                self.assertEqual(response.status_code, 422)
                self.assertEqual(
                    response.json(),
                    {
                        "code": "request_validation_failed",
                        "message": "요청 형식이 올바르지 않습니다. 다시 시도하세요.",
                    },
                )

    def test_login_poll_throttles_early_requests_without_upstream_call(self) -> None:
        with patch.object(
            self.main.auth_service,
            "_request_device_code",
            AsyncMock(
                return_value={
                    "device_code": "device-code",
                    "user_code": "user-code",
                    "verification_uri": "https://github.com/login/device",
                    "verification_uri_complete": "https://github.com/login/device?user_code=user-code",
                    "interval": 5,
                    "expires_in": 900,
                }
            ),
        ):
            start_response = self.client.post("/api/copilot/login/start")

        self.assertEqual(start_response.status_code, 200)
        start_payload = start_response.json()

        with patch.object(self.main.auth_service, "_poll_access_token_once", AsyncMock()) as poll_access_token:
            poll_response = self.client.post(
                "/api/copilot/login/poll",
                json={"loginId": start_payload["loginId"]},
            )

        self.assertEqual(poll_response.status_code, 200)
        poll_payload = poll_response.json()
        poll_access_token.assert_not_called()
        self.assertEqual(poll_payload["status"], "pending")
        self.assertIn("loginId", poll_payload)
        self.assertGreater(poll_payload["retryAfter"], 0)

    def test_login_poll_replay_of_older_login_id_uses_authoritative_backoff(self) -> None:
        with patch.object(
            self.main.auth_service,
            "_request_device_code",
            AsyncMock(
                return_value={
                    "device_code": "device-code",
                    "user_code": "user-code",
                    "verification_uri": "https://github.com/login/device",
                    "verification_uri_complete": "https://github.com/login/device?user_code=user-code",
                    "interval": 5,
                    "expires_in": 900,
                }
            ),
        ):
            start_response = self.client.post("/api/copilot/login/start")

        self.assertEqual(start_response.status_code, 200)
        original_login_id = start_response.json()["loginId"]
        session_secret = self.client.cookies.get(self.main.auth_service.session_cookie_name)
        pending = self.main.auth_service._decrypt_login_ticket(original_login_id, session_secret)
        pending.next_poll_at = time.time() - 1
        self.main.auth_service._pending_login_store.save(pending, now=time.time())

        with patch.object(
            self.main.auth_service,
            "_poll_access_token_once",
            AsyncMock(return_value={"status": "pending", "interval_delta": 5}),
        ) as first_poll:
            refreshed_response = self.client.post(
                "/api/copilot/login/poll",
                json={"loginId": original_login_id},
            )

        self.assertEqual(refreshed_response.status_code, 200)
        first_poll.assert_awaited_once_with("device-code")
        refreshed_payload = refreshed_response.json()
        self.assertEqual(refreshed_payload["status"], "pending")
        self.assertIn("loginId", refreshed_payload)

        refreshed_handle = self.main.auth_service._decrypt_login_handle(refreshed_payload["loginId"])

        with patch.object(self.main.auth_service, "_poll_access_token_once", AsyncMock()) as replay_poll:
            replay_response = self.client.post(
                "/api/copilot/login/poll",
                json={"loginId": original_login_id},
            )

        self.assertEqual(replay_response.status_code, 200)
        replay_poll.assert_not_called()
        replay_payload = replay_response.json()
        self.assertEqual(replay_payload["status"], "pending")
        self.assertGreater(replay_payload["retryAfter"], 0)

        replay_handle = self.main.auth_service._decrypt_login_handle(replay_payload["loginId"])
        self.assertEqual(replay_handle.flow_id, refreshed_handle.flow_id)
        self.assertEqual(replay_handle.version, refreshed_handle.version)

    def test_login_poll_complete_includes_usage_snapshot(self) -> None:
        with patch.object(
            self.main.auth_service,
            "_request_device_code",
            AsyncMock(
                return_value={
                    "device_code": "device-code",
                    "user_code": "user-code",
                    "verification_uri": "https://github.com/login/device",
                    "verification_uri_complete": "https://github.com/login/device?user_code=user-code",
                    "interval": 1,
                    "expires_in": 900,
                }
            ),
        ):
            start_response = self.client.post("/api/copilot/login/start")

        usage_snapshot = make_usage_snapshot(chat_remaining=9, premium_remaining=1)
        expected_session = make_credential_session()
        login_id = start_response.json()["loginId"]
        session_secret = self.client.cookies.get(self.main.auth_service.session_cookie_name)
        pending = self.main.auth_service._decrypt_login_ticket(login_id, session_secret)
        pending.next_poll_at = time.time() - 1
        self.main.auth_service._pending_login_store.save(pending, now=time.time())

        with patch.object(
            self.main.auth_service,
            "_poll_access_token_once",
            AsyncMock(return_value={"status": "complete", "access_token": "github-access-token"}),
        ):
            with patch.object(
                self.main.auth_service,
                "_build_credential_session",
                AsyncMock(return_value=expected_session),
            ):
                with patch.object(
                    self.main.auth_service,
                    "fetch_usage_snapshot",
                    AsyncMock(return_value=usage_snapshot),
                ) as fetch_usage_snapshot:
                    response = self.client.post(
                        "/api/copilot/login/poll",
                        json={"loginId": login_id},
                    )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        fetch_usage_snapshot.assert_awaited_once()
        self.assertEqual(payload["status"], "complete")
        self.assertEqual(payload["usage"]["chatMessages"]["remaining"], 9)
        self.assertEqual(payload["usage"]["premiumRequests"]["remaining"], 1)

    def test_logout_rotates_binding_and_invalidates_existing_envelope(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)

        status_before = self.client.post(
            "/api/copilot/status",
            json={"credentialEnvelope": envelope},
        )
        self.assertTrue(status_before.json()["authenticated"])

        old_cookie_value = self.client.cookies.get(self.main.auth_service.session_cookie_name)
        logout_response = self.client.post("/api/copilot/logout")
        new_cookie_value = self.client.cookies.get(self.main.auth_service.session_cookie_name)

        self.assertEqual(logout_response.status_code, 200)
        self.assertNotEqual(old_cookie_value, new_cookie_value)

        status_after = self.client.post(
            "/api/copilot/status",
            json={"credentialEnvelope": envelope},
        )
        payload = status_after.json()
        self.assertFalse(payload["authenticated"])
        self.assertTrue(payload["shouldClearEnvelope"])
        self.assertEqual(payload["code"], "copilot_credential_binding_mismatch")

    def test_chat_returns_refreshed_envelope_header_when_session_is_refreshed(self) -> None:
        session_secret = self._establish_browser_session()
        stale_envelope = self._issue_envelope(session_secret, expires_at=time.time() - 1)
        refreshed_session = make_credential_session(
            copilot_api_token="refreshed-copilot-token",
            expires_at=time.time() + 3600,
            credential_id="cred-refreshed",
        )
        streamed_session: dict[str, object] = {}

        async def fake_stream_chat_completion(request, model, messages, session):
            streamed_session["session"] = session
            yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        with patch.object(
            self.main.auth_service,
            "_build_credential_session",
            AsyncMock(return_value=refreshed_session),
        ):
            with patch.object(
                self.main.chat_service,
                "stream_chat_completion",
                fake_stream_chat_completion,
            ):
                response = self.client.post(
                    "/api/chat",
                    json={
                        "model": "gpt-5.4",
                        "messages": [{"role": "user", "content": "hello"}],
                        "credentialEnvelope": stale_envelope,
                    },
                )

        refreshed_envelope = response.headers.get("X-Copilot-Credential-Envelope")
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(refreshed_envelope)
        self.assertNotEqual(refreshed_envelope, stale_envelope)
        self.assertEqual(
            streamed_session["session"].copilot_api_token,
            "refreshed-copilot-token",
        )

        refreshed_status = self.client.post(
            "/api/copilot/status",
            json={"credentialEnvelope": refreshed_envelope},
        )
        self.assertTrue(refreshed_status.json()["authenticated"])
        self.assertEqual(refreshed_status.json()["credentialId"], "cred-refreshed")


if __name__ == "__main__":
    unittest.main()