from __future__ import annotations

from _path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

import importlib
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx
from fastapi.testclient import TestClient

from services.copilot_auth import CopilotAuthError, CopilotAuthService


class CopilotUpstreamLoggingTests(unittest.TestCase):
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

    def tearDown(self) -> None:
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def test_json_payload_includes_safe_upstream_context_for_github_403(self) -> None:
        service = CopilotAuthService()
        response = httpx.Response(
            403,
            json={
                "can_signup_for_limited": True,
                "error_details": {
                    "message": "No access to GitHub Copilot found. You are currently logged in as example-user.",
                    "notification_id": "no_copilot_access",
                },
                "access_token": "ghu_secret_should_not_be_logged",
            },
            headers={
                "content-type": "application/json",
                "x-github-request-id": "REQ123",
            },
            request=httpx.Request("GET", "https://api.github.com/copilot_internal/v2/token"),
        )

        with self.assertRaises(CopilotAuthError) as caught:
            service._json_payload(response, "GitHub Copilot 토큰 응답을 해석하지 못했습니다.")

        exc = caught.exception
        self.assertEqual(exc.code, "copilot_upstream_error")
        self.assertEqual(exc.status_code, 403)
        self.assertEqual(exc.log_context["upstream_stage"], "copilot_token")
        self.assertEqual(exc.log_context["upstream_status_code"], 403)
        self.assertEqual(
            exc.log_context["upstream_url"],
            "https://api.github.com/copilot_internal/v2/token",
        )
        self.assertEqual(
            exc.message,
            "현재 로그인한 GitHub 계정(example-user)에 GitHub Copilot 권한이 없습니다. Copilot이 활성화된 계정으로 다시 로그인하거나, 해당 계정에 Copilot 권한이 있는지 확인하세요.",
        )
        self.assertEqual(exc.log_context["upstream_github_request_id"], "REQ123")
        self.assertEqual(
            exc.log_context["upstream_hint"],
            "account_may_not_have_copilot_entitlement",
        )
        self.assertIn("[redacted]", exc.log_context["upstream_body_excerpt"])
        self.assertNotIn(
            "ghu_secret_should_not_be_logged",
            exc.log_context["upstream_body_excerpt"],
        )

    def test_exception_handler_logs_upstream_context_for_copilot_upstream_error(self) -> None:
        main_module = importlib.import_module("main")
        main = importlib.reload(main_module)
        client = TestClient(main.app)
        try:
            with patch.object(main.auth_service, "start_login", AsyncMock()) as start_login:
                start_login.side_effect = CopilotAuthError(
                    status_code=403,
                    message="현재 로그인한 GitHub 계정(lozenia642)에 GitHub Copilot 권한이 없습니다. Copilot이 활성화된 계정으로 다시 로그인하거나, 해당 계정에 Copilot 권한이 있는지 확인하세요.",
                    code="copilot_upstream_error",
                    log_context={
                        "upstream_stage": "copilot_token",
                        "upstream_url": "https://api.github.com/copilot_internal/v2/token",
                        "upstream_status_code": 403,
                        "upstream_hint": "account_may_not_have_copilot_entitlement",
                        "upstream_body_excerpt": '{"message":"User is not enabled for GitHub Copilot."}',
                    },
                )
                with patch.object(main.LOGGER, "warning") as logger_warning:
                    response = client.post("/api/copilot/login/start")

            self.assertEqual(response.status_code, 403)
            self.assertEqual(response.json()["code"], "copilot_upstream_error")
            self.assertIn("현재 로그인한 GitHub 계정(lozenia642)에 GitHub Copilot 권한이 없습니다.", response.json()["message"])
            logger_warning.assert_called_once()
            self.assertEqual(logger_warning.call_args.args[0], "Copilot auth error: %s")
            log_line = logger_warning.call_args.args[1]
            self.assertIn("path=/api/copilot/login/start", log_line)
            self.assertIn("code=copilot_upstream_error", log_line)
            self.assertIn("upstream_stage=copilot_token", log_line)
            self.assertIn("upstream_status_code=403", log_line)
            self.assertIn(
                "upstream_hint=account_may_not_have_copilot_entitlement",
                log_line,
            )
        finally:
            client.close()


if __name__ == "__main__":
    unittest.main()
