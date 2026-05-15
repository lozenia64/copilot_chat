from __future__ import annotations

from _path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

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
from services.copilot_chat import CopilotChatRequestError, CopilotChatService
from services.conversation_service import ConversationService
from services.web_search import WebSearchResult


def make_credential_session(
    *,
    github_access_token: str = "github-access-token",
    copilot_api_token: str = "copilot-api-token",
    expires_at: float | None = None,
    credential_id: str = "cred-123",
    github_user_id: str | None = None,
    github_login: str | None = None,
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
        github_user_id=github_user_id,
        github_login=github_login,
    )


def make_usage_snapshot(
    *,
    status: str = "ok",
    chat_remaining: int | None = 42,
    premium_remaining: int | None = 7,
    premium_total: int | None = None,
    premium_used: int | None = None,
    premium_plan: str | None = None,
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
            "used": None,
            "total": None,
            "plan": None,
            "status": "available" if chat_remaining is not None else "missing",
        },
        "premiumRequests": {
            "remaining": premium_remaining,
            "used": premium_used,
            "total": premium_total,
            "plan": premium_plan,
            "status": "available" if any(value is not None for value in (premium_remaining, premium_total, premium_used)) else "missing",
        },
    }


class FakeCompletionStream:
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
                            "premium_requests_remaining": 85,
                        }
                    },
                    request=httpx.Request("GET", url),
                )

        with patch("services.copilot_auth.httpx.AsyncClient", FakeAsyncClient):
            snapshot = await service.fetch_usage_snapshot("github-access-token")

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["chatMessages"]["remaining"], 18)
        self.assertEqual(snapshot["premiumRequests"]["remaining"], 85)
        self.assertEqual(snapshot["premiumRequests"]["used"], 15)
        self.assertEqual(snapshot["premiumRequests"]["total"], 100)
        self.assertEqual(snapshot["premiumRequests"]["plan"], None)
        self.assertEqual(snapshot["source"], "copilot_user_api")

    async def test_fetch_usage_snapshot_normalizes_remaining_only_premium_quota_fields_from_usage_api(self) -> None:
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
                        "usage": {
                            "remaining_chat_messages": 18,
                            "premium_requests_remaining": 85,
                        }
                    },
                    request=httpx.Request("GET", url),
                )

        with patch("services.copilot_auth.httpx.AsyncClient", FakeAsyncClient):
            snapshot = await service.fetch_usage_snapshot("github-access-token")

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["chatMessages"]["remaining"], 18)
        self.assertEqual(snapshot["premiumRequests"]["remaining"], 85)
        self.assertEqual(snapshot["premiumRequests"]["used"], 15)
        self.assertEqual(snapshot["premiumRequests"]["total"], 100)
        self.assertEqual(snapshot["premiumRequests"]["plan"], None)
        self.assertEqual(snapshot["source"], "copilot_usage_api")

    async def test_fetch_usage_snapshot_normalizes_remaining_only_premium_quota_fields_from_generic_source(self) -> None:
        service = self.service
        service.github_usage_urls = ("https://example.com/internal/copilot/runtime-usage",)

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
                        "usage": {
                            "remaining_chat_messages": 18,
                            "premium_requests_remaining": 85,
                        }
                    },
                    request=httpx.Request("GET", url),
                )

        with patch("services.copilot_auth.httpx.AsyncClient", FakeAsyncClient):
            snapshot = await service.fetch_usage_snapshot("github-access-token")

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["chatMessages"]["remaining"], 18)
        self.assertEqual(snapshot["premiumRequests"]["remaining"], 85)
        self.assertEqual(snapshot["premiumRequests"]["used"], 15)
        self.assertEqual(snapshot["premiumRequests"]["total"], 100)
        self.assertEqual(snapshot["premiumRequests"]["plan"], None)
        self.assertEqual(snapshot["source"], "copilot_usage_other")

    async def test_fetch_usage_snapshot_preserves_authoritative_premium_quota_fields(self) -> None:
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
                            "chat": {"remaining": 18},
                            "premium_requests": {
                                "remaining": 3,
                                "used": 27,
                                "total": 30,
                                "plan": "copilot_pro",
                            },
                        }
                    },
                    request=httpx.Request("GET", url),
                )

        with patch("services.copilot_auth.httpx.AsyncClient", FakeAsyncClient):
            snapshot = await service.fetch_usage_snapshot("github-access-token")

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["premiumRequests"]["remaining"], 3)
        self.assertEqual(snapshot["premiumRequests"]["used"], 27)
        self.assertEqual(snapshot["premiumRequests"]["total"], 30)
        self.assertEqual(snapshot["premiumRequests"]["plan"], "copilot_pro")

    async def test_fetch_usage_snapshot_ignores_zero_total_and_normalizes_remaining_only_premium_quota(self) -> None:
        service = self.service
        service.github_usage_urls = ("https://api.github.com/copilot_internal/user",)

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
                        "usage": {
                            "remaining_chat_messages": 100,
                            "premium_requests_remaining": 83,
                            "premium_requests_limit": 0,
                        }
                    },
                    request=httpx.Request("GET", url),
                )

        with patch("services.copilot_auth.httpx.AsyncClient", FakeAsyncClient):
            snapshot = await service.fetch_usage_snapshot("github-access-token")

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["premiumRequests"]["remaining"], 83)
        self.assertEqual(snapshot["premiumRequests"]["used"], 17)
        self.assertEqual(snapshot["premiumRequests"]["total"], 100)
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

        fake_stream = FakeCompletionStream(
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

    async def test_stream_uses_original_messages_for_initiator_header(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )

        class FakeRequest:
            async def is_disconnected(self) -> bool:
                return False

        class FakeStream:
            def __aiter__(self):
                return self

            async def __anext__(self):
                raise StopAsyncIteration

            async def aclose(self) -> None:
                return None

        with patch(
            "services.copilot_chat.litellm.acompletion",
            AsyncMock(return_value=FakeStream()),
        ) as acompletion:
            chunks = [
                chunk
                async for chunk in service.stream_chat_completion(
                    request=FakeRequest(),
                    model="gpt-5.4",
                    messages=[
                        {"role": "system", "content": "guard"},
                        {"role": "assistant", "content": "reference"},
                        {"role": "user", "content": "서울 날씨를 검색해보고 알려줘"},
                    ],
                    session=make_credential_session(),
                    initiator_messages=[{"role": "user", "content": "서울 날씨를 검색해보고 알려줘"}],
                )
            ]

        self.assertTrue(b"".join(chunks).decode("utf-8").rstrip().endswith("data: [DONE]"))
        self.assertEqual(acompletion.await_args.kwargs["extra_headers"]["X-Initiator"], "user")


class ChatSearchPreparationTests(unittest.IsolatedAsyncioTestCase):
    async def test_prepare_messages_auto_injects_search_context(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )
        service.search_client.search = AsyncMock(
            return_value=[
                WebSearchResult(
                    title="Seoul weather forecast",
                    url="https://weather.example/seoul",
                    snippet="Current temperature and forecast for Seoul.",
                )
            ]
        )
        messages = [{"role": "user", "content": "서울 날씨를 검색해보고 알려줘"}]

        prepared_messages = await service.prepare_messages_for_completion(messages, search_mode="auto")

        service.search_client.search.assert_awaited_once_with("서울 날씨")
        self.assertEqual(prepared_messages[0]["role"], "system")
        self.assertNotIn("Seoul weather forecast", prepared_messages[0]["content"])
        self.assertIn("untrusted external data", prepared_messages[0]["content"])
        self.assertEqual(prepared_messages[1]["role"], "assistant")
        self.assertIn("Seoul weather forecast", prepared_messages[1]["content"])
        self.assertEqual(prepared_messages[2:], messages)

    async def test_prepare_messages_auto_accepts_explicit_english_web_search_request(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )
        service.search_client.search = AsyncMock(
            return_value=[
                WebSearchResult(
                    title="Seoul weather forecast",
                    url="https://weather.example/seoul",
                    snippet="Current temperature and forecast for Seoul.",
                )
            ]
        )
        messages = [{"role": "user", "content": "please search for Seoul weather and summarize it"}]

        prepared_messages = await service.prepare_messages_for_completion(messages, search_mode="auto")

        service.search_client.search.assert_awaited_once_with("Seoul weather")
        self.assertEqual(prepared_messages[2:], messages)

    async def test_prepare_messages_auto_requires_explicit_search_request(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )
        service.search_client.search = AsyncMock(
            return_value=[
                WebSearchResult(
                    title="Ignored result",
                    url="https://example.com/ignored",
                    snippet="ignored",
                )
            ]
        )
        messages = [{"role": "user", "content": "latest weather in Seoul"}]

        prepared_messages = await service.prepare_messages_for_completion(messages, search_mode="auto")

        service.search_client.search.assert_not_awaited()
        self.assertEqual(prepared_messages, messages)

    async def test_prepare_messages_auto_does_not_treat_local_find_analysis_as_web_search(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )
        service.search_client.search = AsyncMock(
            return_value=[
                WebSearchResult(
                    title="Ignored result",
                    url="https://example.com/ignored",
                    snippet="ignored",
                )
            ]
        )
        messages = [{"role": "user", "content": "find the bug in this code"}]

        prepared_messages = await service.prepare_messages_for_completion(messages, search_mode="auto")

        service.search_client.search.assert_not_awaited()
        self.assertEqual(prepared_messages, messages)

    async def test_prepare_messages_auto_does_not_treat_generic_korean_analysis_prompts_as_web_search(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )
        service.search_client.search = AsyncMock(
            return_value=[
                WebSearchResult(
                    title="Ignored result",
                    url="https://example.com/ignored",
                    snippet="ignored",
                )
            ]
        )
        cases = [
            "이 코드 버그를 찾아줘",
            "이 함수 동작 알아봐",
            "로그인 상태 조회해줘",
        ]

        for prompt in cases:
            with self.subTest(prompt=prompt):
                prepared_messages = await service.prepare_messages_for_completion(
                    [{"role": "user", "content": prompt}],
                    search_mode="auto",
                )

                self.assertEqual(prepared_messages, [{"role": "user", "content": prompt}])

        service.search_client.search.assert_not_awaited()

    async def test_prepare_messages_auto_only_considers_latest_user_message(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )
        service.search_client.search = AsyncMock(
            return_value=[
                WebSearchResult(
                    title="Ignored result",
                    url="https://example.com/ignored",
                    snippet="ignored",
                )
            ]
        )
        messages = [
            {"role": "user", "content": "서울 날씨 최신 정보 알려줘"},
            {"role": "assistant", "content": "먼저 상황을 정리해볼게요."},
            {"role": "user", "content": "이전 답변을 두 문장으로 요약해줘"},
        ]

        prepared_messages = await service.prepare_messages_for_completion(messages, search_mode="auto")

        service.search_client.search.assert_not_awaited()
        self.assertEqual(prepared_messages, messages)

    async def test_prepare_messages_auto_falls_back_when_search_fails(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )
        service.search_client.search = AsyncMock(
            side_effect=httpx.ConnectError(
                "duckduckgo unreachable",
                request=httpx.Request("GET", "https://lite.duckduckgo.com/lite/"),
            )
        )
        messages = [{"role": "user", "content": "서울 날씨를 검색해보고 알려줘"}]

        prepared_messages = await service.prepare_messages_for_completion(messages, search_mode="auto")

        self.assertEqual(prepared_messages, messages)


class ChatLoggingContractTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_search_failure_logs_scrubbed_warning(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )
        service.search_client.search = AsyncMock(
            side_effect=httpx.ConnectError(
                "duckduckgo unreachable for https://lite.duckduckgo.com/lite/?q=%EC%84%9C%EC%9A%B8+%EB%82%A0%EC%94%A8",
                request=httpx.Request("GET", "https://lite.duckduckgo.com/lite/?q=%EC%84%9C%EC%9A%B8+%EB%82%A0%EC%94%A8"),
            )
        )
        messages = [{"role": "user", "content": "서울 날씨를 검색해보고 알려줘"}]

        with patch("services.copilot_chat.LOGGER.warning") as logger_warning:
            prepared_messages = await service.prepare_messages_for_completion(messages, search_mode="auto")

        self.assertEqual(prepared_messages, messages)
        logger_warning.assert_called_once_with(
            "Auto search failed; continuing without search context (%s)",
            "network_error",
        )

    async def test_stream_failure_logs_scrubbed_warning(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )

        class FakeRequest:
            async def is_disconnected(self) -> bool:
                return False

        with patch("services.copilot_chat.LOGGER.warning") as logger_warning:
            with patch(
                "services.copilot_chat.litellm.acompletion",
                AsyncMock(
                    side_effect=RuntimeError(
                        "provider exploded at https://api.githubcopilot.com/chat/completions?query=secret"
                    )
                ),
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
        self.assertNotIn("api.githubcopilot.com", payload)
        self.assertNotIn("query=secret", payload)
        self.assertTrue(payload.rstrip().endswith("data: [DONE]"))
        logger_warning.assert_called_once()
        self.assertEqual(
            logger_warning.call_args.args,
            (
                "Copilot chat streaming failed; returning sanitized SSE error (%s)",
                "copilot_chat_stream_failed",
            ),
        )
        self.assertEqual(logger_warning.call_args.kwargs, {})

    async def test_prepare_messages_auto_skips_invalid_or_unsafe_query(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )
        service.search_client.search = AsyncMock(return_value=[])

        control_char_prompt = "search for \x00"
        oversized_prompt = "search for " + ("a" * 400)
        punctuation_only_prompt = "search for !!!"

        for prompt in [control_char_prompt, oversized_prompt, punctuation_only_prompt]:
            with self.subTest(prompt=prompt):
                prepared_messages = await service.prepare_messages_for_completion(
                    [{"role": "user", "content": prompt}],
                    search_mode="auto",
                )
                self.assertEqual(prepared_messages, [{"role": "user", "content": prompt}])

        service.search_client.search.assert_not_awaited()


class ConversationPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_patcher = patch.dict(
            os.environ,
            {
                "COPILOT_CHAT_HISTORY_DB_PATH": str(
                    Path(self.temp_dir.name) / "chat-history-state.sqlite3"
                ),
            },
            clear=False,
        )
        self.env_patcher.start()
        self.service = ConversationService()
        self.scope_token = "history-scope-token"
        self.session = self.service.create_conversation(self.scope_token, "gpt-5.4")

    async def asyncTearDown(self) -> None:
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    async def test_persist_stream_marks_partial_when_error_follows_visible_text(self) -> None:
        turn = self.service.begin_turn(
            self.scope_token,
            self.session["id"],
            content="partial reply please",
            model=None,
        )

        class FakeRequest:
            async def is_disconnected(self) -> bool:
                return False

        async def fake_stream():
            yield b'data: {"choices": [{"delta": {"content": "hel"}}]}\n\n'
            yield 'data: {"code": "copilot_chat_stream_failed", "message": "채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요."}\n\n'.encode("utf-8")
            yield b"data: [DONE]\n\n"

        chunks = [
            chunk
            async for chunk in self.service.persist_stream(
                request=FakeRequest(),
                scope_id=self.scope_token,
                conversation_id=self.session["id"],
                assistant_message_id=turn.assistant_message_id,
                stream=fake_stream(),
            )
        ]

        payload = b"".join(chunks).decode("utf-8")
        self.assertIn('data: {"choices": [{"delta": {"content": "hel"}}]}', payload)
        self.assertIn('data: {"code": "copilot_chat_stream_failed", "message": "채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요."}', payload)

        state = self.service.get_state_payload(self.scope_token)
        restored_session = next(session for session in state["sessions"] if session["id"] == self.session["id"])
        self.assertEqual([message["role"] for message in restored_session["messages"]], ["user", "assistant"])
        self.assertEqual(restored_session["messages"][1]["content"], "hel")
        self.assertEqual(restored_session["messages"][1]["status"], "partial")

    async def test_persist_stream_keeps_reasoning_content_that_the_ui_shows(self) -> None:
        turn = self.service.begin_turn(
            self.scope_token,
            self.session["id"],
            content="show your reasoning",
            model=None,
        )

        class FakeRequest:
            async def is_disconnected(self) -> bool:
                return False

        async def fake_stream():
            yield b'data: {"choices": [{"delta": {"reasoning_content": "step 1"}}]}\n\n'
            yield b'data: {"choices": [{"delta": {"content": " final"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        _ = [
            chunk
            async for chunk in self.service.persist_stream(
                request=FakeRequest(),
                scope_id=self.scope_token,
                conversation_id=self.session["id"],
                assistant_message_id=turn.assistant_message_id,
                stream=fake_stream(),
            )
        ]

        state = self.service.get_state_payload(self.scope_token)
        restored_session = next(session for session in state["sessions"] if session["id"] == self.session["id"])
        self.assertEqual(restored_session["messages"][1]["content"], "step 1 final")
        self.assertEqual(restored_session["messages"][1]["status"], "complete")

    async def test_persist_stream_marks_aborted_reply_and_keeps_visible_partial_text(self) -> None:
        turn = self.service.begin_turn(
            self.scope_token,
            self.session["id"],
            content="abort reply please",
            model=None,
        )

        class FakeRequest:
            async def is_disconnected(self) -> bool:
                return True

        async def fake_stream():
            yield b'data: {"choices": [{"delta": {"content": "stop here"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        _ = [
            chunk
            async for chunk in self.service.persist_stream(
                request=FakeRequest(),
                scope_id=self.scope_token,
                conversation_id=self.session["id"],
                assistant_message_id=turn.assistant_message_id,
                stream=fake_stream(),
            )
        ]

        state = self.service.get_state_payload(self.scope_token)
        restored_session = next(session for session in state["sessions"] if session["id"] == self.session["id"])
        self.assertEqual(restored_session["messages"][1]["content"], "stop here")
        self.assertEqual(restored_session["messages"][1]["status"], "aborted")

    async def test_persist_stream_drops_empty_assistant_message_when_no_visible_text_is_saved(self) -> None:
        turn = self.service.begin_turn(
            self.scope_token,
            self.session["id"],
            content="empty reply please",
            model=None,
        )

        class FakeRequest:
            async def is_disconnected(self) -> bool:
                return False

        async def fake_stream():
            yield 'data: {"code": "copilot_chat_stream_failed", "message": "채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요."}\n\n'.encode("utf-8")
            yield b"data: [DONE]\n\n"

        _ = [
            chunk
            async for chunk in self.service.persist_stream(
                request=FakeRequest(),
                scope_id=self.scope_token,
                conversation_id=self.session["id"],
                assistant_message_id=turn.assistant_message_id,
                stream=fake_stream(),
            )
        ]

        state = self.service.get_state_payload(self.scope_token)
        restored_session = next(session for session in state["sessions"] if session["id"] == self.session["id"])
        self.assertEqual([message["role"] for message in restored_session["messages"]], ["user"])


class ChatSearchModeTests(unittest.TestCase):
    def setUp(self) -> None:
        self.service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )

    def test_normalize_search_mode_defaults_to_off(self) -> None:
        self.assertEqual(self.service.normalize_search_mode(None), "off")

    def test_normalize_search_mode_accepts_explicit_off(self) -> None:
        self.assertEqual(self.service.normalize_search_mode("off"), "off")

    def test_normalize_search_mode_rejects_blank_string(self) -> None:
        with self.assertRaisesRegex(CopilotChatRequestError, "검색 모드가 올바르지 않습니다"):
            self.service.normalize_search_mode("   ")


class FrontendUsageContractTests(unittest.TestCase):
    def test_usage_summary_ignores_snapshot_detail_in_ui_code(self) -> None:
        source = (Path(__file__).resolve().parent / "static" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("snapshot.detail", source)
        self.assertIn("USAGE_SUMMARY_REASON_MESSAGES", source)
        self.assertNotIn("USAGE_VISUAL_CONFIG.premiumRequests.total", source)
        self.assertIn("normalizeUsageQuantity(metric?.total)", source)

    def test_premium_usage_card_prefers_used_percent_when_total_basis_exists(self) -> None:
        source = (Path(__file__).resolve().parent / "static" / "app.js").read_text(encoding="utf-8")

        self.assertIn("function formatPremiumUsagePrimaryValue(snapshot, metric)", source)
        self.assertIn("badge: formatPremiumUsagePrimaryValue(snapshot, metric)", source)
        self.assertIn("elements.authPremiumRequestsRemaining.textContent = formatPremiumUsagePrimaryValue(usage, usage.premiumRequests);", source)
        self.assertIn("primaryValue: `${usedPercent}% 사용`", source)
        self.assertIn("title: `Premium requests ${usedPercent}% 사용`", source)
        self.assertNotIn("${remainingText}% 남음", source)

    def test_browser_visible_premium_ui_does_not_render_plan_metadata(self) -> None:
        source = (Path(__file__).resolve().parent / "static" / "app.js").read_text(encoding="utf-8")

        self.assertNotIn("plan ${metric.plan}", source)
        self.assertNotIn("return `plan ${metric.plan}`;", source)


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
                "COPILOT_CHAT_HISTORY_DB_PATH": str(
                    Path(self.temp_dir.name) / "chat-history-state.sqlite3"
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

    def _create_conversation(self, model: str = "gpt-5.4") -> dict[str, object]:
        response = self.client.post(
            "/api/conversations",
            json={"model": model},
        )
        self.assertEqual(response.status_code, 200)
        return response.json()

    def test_conversations_route_issues_auth_session_cookie_only(self) -> None:
        response = self.client.get("/api/conversations")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"sessions": [], "activeSessionId": None})
        set_cookie_header = response.headers.get("set-cookie", "")
        self.assertIn(self.main.auth_service.session_cookie_name, set_cookie_header)
        self.assertNotIn("copilot_history_scope", set_cookie_header)

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
                        "used": None,
                        "total": None,
                        "plan": None,
                        "status": "missing",
                    },
                    "premiumRequests": {
                        "remaining": None,
                        "used": None,
                        "total": None,
                        "plan": None,
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
        usage_snapshot = make_usage_snapshot(
            chat_remaining=15,
            premium_remaining=2,
            premium_total=10,
            premium_used=8,
            premium_plan="copilot_pro",
        )

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
        self.assertEqual(payload["usage"]["premiumRequests"]["used"], 8)
        self.assertEqual(payload["usage"]["premiumRequests"]["total"], 10)
        self.assertEqual(payload["usage"]["premiumRequests"]["plan"], "copilot_pro")

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

    def test_proxy_chat_returns_json_payload_using_hardcoded_server_credentials(self) -> None:
        streamed_request: dict[str, object] = {}

        async def fake_stream_chat_completion(request, model, messages, session, initiator_messages=None, **kwargs):
            streamed_request["model"] = model
            streamed_request["messages"] = messages
            streamed_request["initiator_messages"] = initiator_messages
            streamed_request["session"] = session
            yield b'data: {"choices":[{"delta":{"content":"hello "}}]}\n\n'
            yield b'data: {"choices":[{"delta":{"content":"world"}}]}\n\n'
            yield (
                'data: {"type":"assistant_sources","sources":[{"title":"Example","url":"https://example.com"}]}\n\n'
            ).encode("utf-8")
            yield b"data: [DONE]\n\n"

        with patch.dict(
            os.environ,
            {
                "COPILOT_CREDENTIAL_ENVELOPE": "hardcoded-envelope",
                "COPILOT_SESSION_COOKIE_VALUE": "hardcoded-session-cookie",
            },
            clear=False,
        ):
            with patch.object(
                self.main.auth_service,
                "resolve_session",
                AsyncMock(return_value=(make_credential_session(), "refreshed-envelope")),
            ) as resolve_session:
                with patch.object(
                    self.main.chat_service,
                    "stream_chat_completion",
                    fake_stream_chat_completion,
                ):
                    response = self.client.post(
                        "/api/proxy/chat",
                        json={"MODEL": "gpt-5.4", "PROMPT": "  hello world  "},
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers.get("X-Copilot-Credential-Envelope"), "refreshed-envelope")
        self.assertEqual(
            response.json(),
            {
                "model": "gpt-5.4",
                "prompt": "hello world",
                "content": "hello world",
                "sources": [{"title": "Example", "url": "https://example.com"}],
                "meta": {"credentialEnvelopeRefreshed": True},
            },
        )
        resolve_session.assert_awaited_once_with(
            "hardcoded-envelope",
            "hardcoded-session-cookie",
        )
        self.assertEqual(streamed_request["model"], "gpt-5.4")
        self.assertEqual(
            streamed_request["messages"],
            [{"role": "user", "content": "hello world"}],
        )
        self.assertEqual(
            streamed_request["initiator_messages"],
            [{"role": "user", "content": "hello world"}],
        )

    def test_proxy_chat_accepts_lowercase_request_keys(self) -> None:
        async def fake_stream_chat_completion(request, model, messages, session, initiator_messages=None, **kwargs):
            yield b'data: {"choices":[{"delta":{"content":"ok"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        with patch.dict(
            os.environ,
            {
                "COPILOT_CREDENTIAL_ENVELOPE": "hardcoded-envelope",
                "COPILOT_SESSION_COOKIE_VALUE": "hardcoded-session-cookie",
            },
            clear=False,
        ):
            with patch.object(
                self.main.auth_service,
                "resolve_session",
                AsyncMock(return_value=(make_credential_session(), None)),
            ):
                with patch.object(
                    self.main.chat_service,
                    "stream_chat_completion",
                    fake_stream_chat_completion,
                ):
                    response = self.client.post(
                        "/api/proxy/chat",
                        json={"model": "gpt-5.4", "prompt": "오늘 날씨는?"},
                    )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["model"], "gpt-5.4")
        self.assertEqual(response.json()["prompt"], "오늘 날씨는?")
        self.assertEqual(response.json()["content"], "ok")

    def test_proxy_chat_returns_json_error_when_stream_emits_error_payload(self) -> None:
        async def fake_stream_chat_completion(request, model, messages, session, initiator_messages=None, **kwargs):
            yield b'data: {"choices":[{"delta":{"content":"partial"}}]}\n\n'
            yield (
                'data: {"code":"copilot_chat_stream_failed","message":"채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요."}\n\n'
            ).encode("utf-8")
            yield b"data: [DONE]\n\n"

        with patch.dict(
            os.environ,
            {
                "COPILOT_CREDENTIAL_ENVELOPE": "hardcoded-envelope",
                "COPILOT_SESSION_COOKIE_VALUE": "hardcoded-session-cookie",
            },
            clear=False,
        ):
            with patch.object(
                self.main.auth_service,
                "resolve_session",
                AsyncMock(return_value=(make_credential_session(), None)),
            ):
                with patch.object(
                    self.main.chat_service,
                    "stream_chat_completion",
                    fake_stream_chat_completion,
                ):
                    response = self.client.post(
                        "/api/proxy/chat",
                        json={"MODEL": "gpt-5.4", "PROMPT": "hello"},
                    )

        self.assertEqual(response.status_code, 502)
        self.assertEqual(
            response.json(),
            {
                "code": "copilot_chat_stream_failed",
                "message": "채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요.",
            },
        )

    def test_proxy_chat_requires_server_side_hardcoded_credentials(self) -> None:
        with patch.dict(
            os.environ,
            {
                "COPILOT_CREDENTIAL_ENVELOPE": "",
                "COPILOT_SESSION_COOKIE_VALUE": "",
            },
            clear=False,
        ):
            response = self.client.post(
                "/api/proxy/chat",
                json={"MODEL": "gpt-5.4", "PROMPT": "hello"},
            )

        self.assertEqual(response.status_code, 503)
        self.assertEqual(
            response.json(),
            {
                "code": "proxy_chat_config_missing",
                "message": "프록시 채팅용 Copilot 자격 정보가 서버에 설정되지 않았습니다. 환경 변수를 확인하세요.",
            },
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

    def test_login_poll_from_different_browser_session_logs_session_mismatch_details(self) -> None:
        with patch.object(
            self.main.auth_service,
            "_request_device_code",
            AsyncMock(
                return_value={
                    "device_code": "device-code",
                    "user_code": "user-code",
                    "verification_uri": "https://github.com/login/device",
                    "interval": 5,
                    "expires_in": 900,
                }
            ),
        ):
            start_response = self.client.post("/api/copilot/login/start")

        self.assertEqual(start_response.status_code, 200)
        login_id = start_response.json()["loginId"]

        other_client = TestClient(self.main.app)
        try:
            with patch.object(self.main.LOGGER, "warning") as logger_warning:
                response = other_client.post(
                    "/api/copilot/login/poll",
                    json={"loginId": login_id},
                )

            self.assertEqual(response.status_code, 403)
            payload = response.json()
            self.assertEqual(payload["code"], "copilot_login_session_mismatch")
            self.assertEqual(
                payload["message"],
                "현재 브라우저 세션과 맞지 않는 로그인 요청입니다. 다시 시도하세요.",
            )

            logger_warning.assert_called_once()
            self.assertEqual(logger_warning.call_args.args[0], "Copilot auth error: %s")
            log_line = logger_warning.call_args.args[1]
            self.assertIn("path=/api/copilot/login/poll", log_line)
            self.assertIn("code=copilot_login_session_mismatch", log_line)
            self.assertIn("browser_session_cookie_created=True", log_line)
            self.assertIn("reason=browser_session_changed_or_missing_cookie", log_line)
        finally:
            other_client.close()

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

    def test_chat_validation_rejects_unknown_search_mode(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)

        with patch.object(
            self.main.auth_service,
            "resolve_session",
            AsyncMock(return_value=(make_credential_session(), None)),
        ):
            response = self.client.post(
                "/api/chat",
                json={
                    "model": "gpt-5.4",
                    "messages": [{"role": "user", "content": "hello"}],
                    "credentialEnvelope": envelope,
                    "searchMode": "always",
                },
            )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["code"], "chat_search_mode_invalid")
        self.assertEqual(payload["message"], "검색 모드가 올바르지 않습니다. 다시 시도하세요.")

    def test_chat_validation_rejects_blank_search_mode(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)

        with patch.object(
            self.main.auth_service,
            "resolve_session",
            AsyncMock(return_value=(make_credential_session(), None)),
        ):
            response = self.client.post(
                "/api/chat",
                json={
                    "model": "gpt-5.4",
                    "messages": [{"role": "user", "content": "hello"}],
                    "credentialEnvelope": envelope,
                    "searchMode": "   ",
                },
            )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["code"], "chat_search_mode_invalid")

    def test_conversation_message_validation_rejects_unknown_search_mode(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)
        user_scope = self.main.auth_service.get_user_history_scope("user-123")
        conversation_payload = {"session": self.main.conversation_service.create_conversation(user_scope, "gpt-5.4")}

        with patch.object(
            self.main.auth_service,
            "resolve_session",
            AsyncMock(return_value=(make_credential_session(github_user_id="user-123"), None)),
        ):
            response = self.client.post(
                f"/api/conversations/{conversation_payload['session']['id']}/messages",
                json={
                    "content": "hello",
                    "model": "gpt-5.4",
                    "credentialEnvelope": envelope,
                    "searchMode": "always",
                },
            )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["code"], "chat_search_mode_invalid")
        self.assertEqual(payload["message"], "검색 모드가 올바르지 않습니다. 다시 시도하세요.")

    def test_chat_validation_rejects_invalid_tool_choice(self) -> None:
        response = self.client.post(
            "/api/chat",
            json={
                "model": "gpt-5.4",
                "messages": [{"role": "user", "content": "hello"}],
                "credentialEnvelope": "opaque-value",
                "tool_choice": "force",
            },
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["code"], "chat_tool_choice_invalid")

    def test_chat_validation_rejects_too_many_tools(self) -> None:
        tools = [
            {
                "type": "function",
                "function": {
                    "name": f"tool_{i}",
                    "description": "d",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for i in range(17)
        ]
        response = self.client.post(
            "/api/chat",
            json={
                "model": "gpt-5.4",
                "messages": [{"role": "user", "content": "hello"}],
                "credentialEnvelope": "opaque-value",
                "tools": tools,
            },
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["code"], "chat_tools_invalid")

    def test_chat_validation_rejects_invalid_parallel_tool_calls(self) -> None:
        response = self.client.post(
            "/api/chat",
            json={
                "model": "gpt-5.4",
                "messages": [{"role": "user", "content": "hello"}],
                "credentialEnvelope": "opaque-value",
                "parallel_tool_calls": {},
            },
        )

        self.assertEqual(response.status_code, 400)
        payload = response.json()
        self.assertEqual(payload["code"], "request_validation_failed")

    def test_chat_omitted_search_mode_forwards_none_to_streaming_service(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)
        captured_search_modes: list[str | None] = []

        async def fake_stream_chat_completion(request, model, messages, session, initiator_messages=None, **kwargs):
            captured_search_modes.append(kwargs.get("search_mode"))
            yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'

        with patch.object(
            self.main.auth_service,
            "resolve_session",
            AsyncMock(return_value=(make_credential_session(github_user_id="user-123"), None)),
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
                        "credentialEnvelope": envelope,
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured_search_modes, [None])

    def test_chat_explicit_off_search_mode_forwards_off_to_streaming_service(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)
        captured_search_modes: list[str | None] = []

        async def fake_stream_chat_completion(request, model, messages, session, initiator_messages=None, **kwargs):
            captured_search_modes.append(kwargs.get("search_mode"))
            yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'

        with patch.object(
            self.main.auth_service,
            "resolve_session",
            AsyncMock(return_value=(make_credential_session(), None)),
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
                        "credentialEnvelope": envelope,
                        "searchMode": "off",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(captured_search_modes, ["off"])

    def test_malformed_request_bodies_return_validation_error_contract(self) -> None:
        cases = [
            ("/api/copilot/status", []),
            ("/api/copilot/login/poll", {}),
            ("/api/conversations", []),
            ("/api/conversations/conv-test/model", []),
            (
                "/api/conversations/conv-test/messages",
                {
                    "model": "gpt-5.4",
                    "credentialEnvelope": "opaque-value",
                },
            ),
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

                self.assertEqual(response.status_code, 400)
                self.assertEqual(
                    response.json(),
                    {
                        "code": "request_validation_failed",
                        "message": "요청 형식이 올바르지 않습니다. 다시 시도하세요.",
                    },
                )

    def test_index_response_uses_versioned_assets_and_no_store_headers(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn('/static/style.css?v=', response.text)
        self.assertIn('/static/app.js?v=', response.text)
        self.assertEqual(response.headers.get("cache-control"), "no-store, max-age=0")
        self.assertEqual(response.headers.get("pragma"), "no-cache")

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

        usage_snapshot = make_usage_snapshot(
            chat_remaining=9,
            premium_remaining=1,
            premium_total=12,
            premium_used=11,
            premium_plan="copilot_business",
        )
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
        self.assertEqual(payload["usage"]["premiumRequests"]["used"], 11)
        self.assertEqual(payload["usage"]["premiumRequests"]["total"], 12)
        self.assertEqual(payload["usage"]["premiumRequests"]["plan"], "copilot_business")

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

    def test_logout_clears_conversation_restore_for_current_browser(self) -> None:
        self._establish_browser_session()
        created = self._create_conversation()

        before_logout = self.client.get("/api/conversations")
        self.assertEqual(before_logout.status_code, 200)
        self.assertEqual(len(before_logout.json()["sessions"]), 1)
        self.assertEqual(before_logout.json()["sessions"][0]["id"], created["session"]["id"])

        old_cookie_value = self.client.cookies.get(self.main.auth_service.session_cookie_name)
        logout_response = self.client.post("/api/copilot/logout")
        new_cookie_value = self.client.cookies.get(self.main.auth_service.session_cookie_name)

        self.assertEqual(logout_response.status_code, 200)
        self.assertNotEqual(old_cookie_value, new_cookie_value)

        after_logout = self.client.get("/api/conversations")
        self.assertEqual(after_logout.status_code, 200)
        self.assertEqual(after_logout.json(), {"sessions": [], "activeSessionId": None})

    def test_chat_history_uses_separate_sqlite_file_from_pending_login_state(self) -> None:
        self.assertNotEqual(
            self.main.auth_service.pending_login_db_path,
            self.main.conversation_service.history_db_path,
        )
        self.assertTrue(self.main.auth_service.pending_login_db_path.exists())
        self.assertTrue(self.main.conversation_service.history_db_path.exists())

    def test_conversation_model_update_rejects_disallowed_model(self) -> None:
        self._establish_browser_session()
        conversation_payload = self._create_conversation()

        response = self.client.post(
            f"/api/conversations/{conversation_payload['session']['id']}/model",
            json={"model": "not-allowed-model"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "chat_model_not_allowed")

    def test_conversation_model_update_returns_404_for_unknown_conversation(self) -> None:
        self._establish_browser_session()

        response = self.client.post(
            "/api/conversations/conv-missing/model",
            json={"model": "gpt-5.4"},
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "conversation_not_found")

    def test_conversation_activate_returns_404_for_unknown_conversation(self) -> None:
        self._establish_browser_session()

        response = self.client.post("/api/conversations/conv-missing/activate")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "conversation_not_found")

    def test_conversation_message_requires_visible_content(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)
        conversation_payload = self._create_conversation()

        response = self.client.post(
            f"/api/conversations/{conversation_payload['session']['id']}/messages",
            json={
                "content": "   ",
                "model": "gpt-5.4",
                "credentialEnvelope": envelope,
            },
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(response.json()["code"], "conversation_message_required")

    def test_conversation_message_returns_404_for_unknown_conversation(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)

        response = self.client.post(
            "/api/conversations/conv-missing/messages",
            json={
                "content": "hello",
                "model": "gpt-5.4",
                "credentialEnvelope": envelope,
            },
        )

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.json()["code"], "conversation_not_found")

    def test_conversation_message_persists_visible_transcript_only(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)
        user_scope = self.main.auth_service.get_user_history_scope("user-123")
        conversation_payload = {"session": self.main.conversation_service.create_conversation(user_scope, "gpt-5.4")}
        conversation_id = conversation_payload["session"]["id"]
        streamed_request: dict[str, object] = {}

        async def fake_stream_chat_completion(request, model, messages, session, initiator_messages=None, **kwargs):
            streamed_request["messages"] = messages
            streamed_request["initiator_messages"] = initiator_messages
            streamed_request["search_mode"] = kwargs.get("search_mode")
            yield b'data: {"choices":[{"delta":{"content":"search-backed answer"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        with patch.object(
            self.main.auth_service,
            "resolve_session",
            AsyncMock(return_value=(make_credential_session(github_user_id="user-123"), None)),
        ):
            with patch.object(
                self.main.chat_service,
                "stream_chat_completion",
                fake_stream_chat_completion,
            ):
                response = self.client.post(
                    f"/api/conversations/{conversation_id}/messages",
                    json={
                        "content": "서울 날씨를 검색해보고 알려줘",
                        "model": "gpt-5.4",
                        "credentialEnvelope": envelope,
                        "searchMode": "auto",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            streamed_request,
            {
                "messages": [{"role": "user", "content": "서울 날씨를 검색해보고 알려줘"}],
                "initiator_messages": [{"role": "user", "content": "서울 날씨를 검색해보고 알려줘"}],
                "search_mode": "auto",
            },
        )

        history_response = self.main.conversation_service.get_state_payload(user_scope)
        session_payload = next(
            session
            for session in history_response["sessions"]
            if session["id"] == conversation_id
        )
        self.assertEqual([message["role"] for message in session_payload["messages"]], ["user", "assistant"])
        self.assertEqual(session_payload["messages"][0]["content"], "서울 날씨를 검색해보고 알려줘")
        self.assertEqual(session_payload["messages"][1]["content"], "search-backed answer")

    def test_conversations_restore_sessions_active_session_messages_and_model_after_refresh(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)
        first_conversation = self._create_conversation()
        second_conversation = self._create_conversation()
        first_conversation_id = first_conversation["session"]["id"]
        second_conversation_id = second_conversation["session"]["id"]

        activate_response = self.client.post(f"/api/conversations/{first_conversation_id}/activate")
        self.assertEqual(activate_response.status_code, 200)
        self.assertEqual(activate_response.json()["activeSessionId"], first_conversation_id)

        async def fake_stream_chat_completion(request, model, messages, session, initiator_messages=None, **kwargs):
            yield b'data: {"choices":[{"delta":{"content":"restored answer"}}]}\n\n'
            yield b"data: [DONE]\n\n"

        with patch.object(
            self.main.chat_service,
            "stream_chat_completion",
            fake_stream_chat_completion,
        ):
            send_response = self.client.post(
                f"/api/conversations/{first_conversation_id}/messages",
                json={
                    "content": "refresh-safe question",
                    "model": "gpt-5.4",
                    "credentialEnvelope": envelope,
                },
            )

        self.assertEqual(send_response.status_code, 200)
        session_cookie = self.client.cookies.get(self.main.auth_service.session_cookie_name)
        self.assertIsNotNone(session_cookie)

        reloaded_main = importlib.reload(self.main)
        with TestClient(reloaded_main.app) as refreshed_client:
            refreshed_client.cookies.set(
                reloaded_main.auth_service.session_cookie_name,
                session_cookie,
            )
            restored_response = refreshed_client.get("/api/conversations")

        self.assertEqual(restored_response.status_code, 200)
        restored_payload = restored_response.json()
        self.assertEqual(restored_payload["activeSessionId"], first_conversation_id)
        self.assertEqual(len(restored_payload["sessions"]), 2)
        restored_first_conversation = next(
            session
            for session in restored_payload["sessions"]
            if session["id"] == first_conversation_id
        )
        restored_second_conversation = next(
            session
            for session in restored_payload["sessions"]
            if session["id"] == second_conversation_id
        )
        self.assertEqual(restored_first_conversation["model"], "gpt-5.4")
        self.assertEqual(
            [message["content"] for message in restored_first_conversation["messages"]],
            ["refresh-safe question", "restored answer"],
        )
        self.assertEqual(restored_second_conversation["messages"], [])

    def test_conversation_message_returns_refreshed_envelope_header_when_session_is_refreshed(self) -> None:
        session_secret = self._establish_browser_session()
        stale_envelope = self._issue_envelope(session_secret, expires_at=time.time() - 1)
        refreshed_session = make_credential_session(
            copilot_api_token="refreshed-conversation-token",
            expires_at=time.time() + 3600,
            credential_id="cred-conversation-refreshed",
        )
        conversation_payload = self._create_conversation()
        streamed_session: dict[str, object] = {}

        async def fake_stream_chat_completion(request, model, messages, session, initiator_messages=None, **kwargs):
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
                    f"/api/conversations/{conversation_payload['session']['id']}/messages",
                    json={
                        "content": "hello",
                        "model": "gpt-5.4",
                        "credentialEnvelope": stale_envelope,
                    },
                )

        refreshed_envelope = response.headers.get("X-Copilot-Credential-Envelope")
        self.assertEqual(response.status_code, 200)
        self.assertIsNotNone(refreshed_envelope)
        self.assertNotEqual(refreshed_envelope, stale_envelope)
        self.assertEqual(
            streamed_session["session"].copilot_api_token,
            "refreshed-conversation-token",
        )

        refreshed_status = self.client.post(
            "/api/copilot/status",
            json={"credentialEnvelope": refreshed_envelope},
        )
        self.assertTrue(refreshed_status.json()["authenticated"])
        self.assertEqual(refreshed_status.json()["credentialId"], "cred-conversation-refreshed")

    def test_conversation_message_sse_error_contract_matches_raw_chat(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)
        conversation_payload = self._create_conversation()

        async def fake_stream_chat_completion(request, model, messages, session, initiator_messages=None, **kwargs):
            yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'
            yield (
                'data: {"code": "copilot_chat_stream_failed", "message": '
                '"채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요."}\n\n'
            ).encode("utf-8")
            yield b"data: [DONE]\n\n"

        with patch.object(
            self.main.chat_service,
            "stream_chat_completion",
            fake_stream_chat_completion,
        ):
            response = self.client.post(
                f"/api/conversations/{conversation_payload['session']['id']}/messages",
                json={
                    "content": "hello",
                    "model": "gpt-5.4",
                    "credentialEnvelope": envelope,
                },
            )

        self.assertEqual(response.status_code, 200)
        self.assertIn('data: {"choices":[{"delta":{"content":"hello"}}]}', response.text)
        self.assertIn(
            'data: {"code": "copilot_chat_stream_failed", "message": "채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요."}',
            response.text,
        )
        self.assertTrue(response.text.endswith("data: [DONE]\n\n"))

    def test_chat_returns_refreshed_envelope_header_when_session_is_refreshed(self) -> None:
        session_secret = self._establish_browser_session()
        stale_envelope = self._issue_envelope(session_secret, expires_at=time.time() - 1)
        refreshed_session = make_credential_session(
            copilot_api_token="refreshed-copilot-token",
            expires_at=time.time() + 3600,
            credential_id="cred-refreshed",
        )
        streamed_session: dict[str, object] = {}

        async def fake_stream_chat_completion(request, model, messages, session, initiator_messages=None, **kwargs):
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

        refreshed_session = self.main.auth_service._decrypt_envelope(
            refreshed_envelope,
            session_secret,
        )
        self.assertEqual(refreshed_session.credential_id, "cred-refreshed")

    def test_chat_auto_search_forwards_raw_messages_and_search_mode_to_service(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)
        streamed_request: dict[str, object] = {}

        async def fake_stream_chat_completion(request, model, messages, session, initiator_messages=None, **kwargs):
            streamed_request["messages"] = messages
            streamed_request["initiator_messages"] = initiator_messages
            streamed_request["search_mode"] = kwargs.get("search_mode")
            yield b'data: {"choices":[{"delta":{"content":"hello"}}]}\n\n'

        with patch.object(
            self.main.auth_service,
            "resolve_session",
            AsyncMock(return_value=(make_credential_session(), None)),
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
                        "messages": [{"role": "user", "content": "서울 날씨 알려줘"}],
                        "credentialEnvelope": envelope,
                        "searchMode": "auto",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            streamed_request,
            {
                "messages": [{"role": "user", "content": "서울 날씨 알려줘"}],
                "initiator_messages": [{"role": "user", "content": "서울 날씨 알려줘"}],
                "search_mode": "auto",
            },
        )

    def test_chat_auto_search_stream_passthroughs_delta_and_done(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)
        fake_stream = FakeCompletionStream(
            [
                {"choices": [{"delta": {"content": "hello"}}]},
                {"choices": [{"delta": {"content": " world"}}]},
            ]
        )

        with patch.object(
            self.main.chat_service.search_client,
            "search",
            AsyncMock(
                return_value=[
                    WebSearchResult(
                        title="Seoul weather forecast",
                        url="https://weather.example/seoul",
                        snippet="Current temperature and forecast for Seoul.",
                    )
                ]
            ),
        ) as search:
            with patch(
                "services.copilot_chat.litellm.acompletion",
                AsyncMock(return_value=fake_stream),
            ) as acompletion:
                response = self.client.post(
                    "/api/chat",
                    json={
                        "model": "gpt-5.4",
                        "messages": [{"role": "user", "content": "서울 날씨를 검색해보고 알려줘"}],
                        "credentialEnvelope": envelope,
                        "searchMode": "auto",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.text,
            'data: {"choices": [{"delta": {"content": "hello"}}]}\n\n'
            'data: {"choices": [{"delta": {"content": " world"}}]}\n\n'
            'data: [DONE]\n\n',
        )
        search.assert_awaited_once_with("서울 날씨")
        streamed_messages = acompletion.await_args.kwargs["messages"]
        self.assertEqual(streamed_messages[0]["role"], "system")
        self.assertEqual(streamed_messages[1]["role"], "assistant")
        self.assertEqual(streamed_messages[2:], [{"role": "user", "content": "서울 날씨를 검색해보고 알려줘"}])
        self.assertTrue(fake_stream.closed)

    def test_chat_auto_search_stream_error_returns_sanitized_sse_and_done(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)
        fake_stream = FakeCompletionStream(
            [
                {"choices": [{"delta": {"content": "hello"}}]},
                {
                    "error": {"message": "provider exploded", "type": "upstream_error"},
                    "detail": "https://api.githubcopilot.com/chat/completions?query=secret",
                },
            ]
        )

        with patch.object(
            self.main.chat_service.search_client,
            "search",
            AsyncMock(
                return_value=[
                    WebSearchResult(
                        title="Seoul weather forecast",
                        url="https://weather.example/seoul",
                        snippet="Current temperature and forecast for Seoul.",
                    )
                ]
            ),
        ):
            with patch(
                "services.copilot_chat.litellm.acompletion",
                AsyncMock(return_value=fake_stream),
            ):
                response = self.client.post(
                    "/api/chat",
                    json={
                        "model": "gpt-5.4",
                        "messages": [{"role": "user", "content": "서울 날씨를 검색해보고 알려줘"}],
                        "credentialEnvelope": envelope,
                        "searchMode": "auto",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertIn('data: {"choices": [{"delta": {"content": "hello"}}]}', response.text)
        self.assertIn(
            'data: {"code": "copilot_chat_stream_failed", "message": "채팅 응답을 생성하지 못했습니다. 잠시 후 다시 시도하세요."}',
            response.text,
        )
        self.assertNotIn("provider exploded", response.text)
        self.assertNotIn("api.githubcopilot.com", response.text)
        self.assertTrue(response.text.endswith("data: [DONE]\n\n"))
        self.assertTrue(fake_stream.closed)

    def test_chat_auto_search_empty_results_falls_back_to_ordinary_chat(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(session_secret)
        fake_stream = FakeCompletionStream(
            [
                {"choices": [{"delta": {"content": "ordinary"}}]},
            ]
        )
        original_messages = [{"role": "user", "content": "서울 날씨를 검색해보고 알려줘"}]

        with patch.object(
            self.main.chat_service.search_client,
            "search",
            AsyncMock(return_value=[]),
        ) as search:
            with patch(
                "services.copilot_chat.litellm.acompletion",
                AsyncMock(return_value=fake_stream),
            ) as acompletion:
                response = self.client.post(
                    "/api/chat",
                    json={
                        "model": "gpt-5.4",
                        "messages": original_messages,
                        "credentialEnvelope": envelope,
                        "searchMode": "auto",
                    },
                )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.text,
            'data: {"choices": [{"delta": {"content": "ordinary"}}]}\n\n'
            'data: [DONE]\n\n',
        )
        search.assert_awaited_once_with("서울 날씨")
        self.assertEqual(acompletion.await_args.kwargs["messages"], original_messages)
        self.assertTrue(fake_stream.closed)


if __name__ == "__main__":
    unittest.main()