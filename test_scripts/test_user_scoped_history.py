from __future__ import annotations

from _path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

import importlib
import os
import sqlite3
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

from services.conversation_service import ConversationService
from services.copilot_auth import CopilotCredentialSession


def make_credential_session(
    *,
    github_user_id: str | None,
    github_login: str | None,
    credential_id: str = "cred-123",
) -> CopilotCredentialSession:
    now = time.time()
    return CopilotCredentialSession(
        github_access_token="github-access-token",
        copilot_api_token="copilot-api-token",
        copilot_api_expires_at=now + 3600,
        copilot_api_base="https://api.githubcopilot.com",
        issued_at=now,
        updated_at=now,
        credential_id=credential_id,
        github_user_id=github_user_id,
        github_login=github_login,
    )


class UserScopedHistoryApiTests(unittest.TestCase):
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

    def _establish_browser_session(self, client: TestClient | None = None) -> str:
        test_client = client or self.client
        response = test_client.post(
            "/api/copilot/status",
            json={"credentialEnvelope": None},
        )
        self.assertEqual(response.status_code, 200)
        session_secret = test_client.cookies.get(self.main.auth_service.session_cookie_name)
        self.assertIsNotNone(session_secret)
        return str(session_secret)

    def _issue_envelope(
        self,
        session_secret: str,
        *,
        github_user_id: str | None,
        github_login: str | None,
        credential_id: str = "cred-123",
    ) -> str:
        session = make_credential_session(
            github_user_id=github_user_id,
            github_login=github_login,
            credential_id=credential_id,
        )
        return self.main.auth_service._encrypt_session(session, session_secret)

    def test_status_refreshes_old_envelope_with_github_identity(self) -> None:
        session_secret = self._establish_browser_session()
        legacy_envelope = self._issue_envelope(
            session_secret,
            github_user_id=None,
            github_login=None,
        )

        with patch.object(
            self.main.auth_service,
            "_request_github_user_profile",
            AsyncMock(return_value=("12345", "octocat")),
        ):
            response = self.client.post(
                "/api/copilot/status",
                json={"credentialEnvelope": legacy_envelope},
            )

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()["authenticated"])
        refreshed_envelope = response.headers.get("X-Copilot-Credential-Envelope")
        self.assertIsNotNone(refreshed_envelope)
        refreshed_session = self.main.auth_service._decrypt_envelope(
            str(refreshed_envelope),
            session_secret,
        )
        self.assertEqual(refreshed_session.github_user_id, "12345")
        self.assertEqual(refreshed_session.github_login, "octocat")

    def test_authenticated_history_restores_after_logout_and_relogin(self) -> None:
        session_secret = self._establish_browser_session()
        envelope = self._issue_envelope(
            session_secret,
            github_user_id="1001",
            github_login="octocat",
        )

        created = self.client.post(
            "/api/conversations",
            json={"model": "gpt-5.4", "credentialEnvelope": envelope},
        )
        self.assertEqual(created.status_code, 200)
        created_session_id = created.json()["session"]["id"]

        logout_response = self.client.post("/api/copilot/logout")
        self.assertEqual(logout_response.status_code, 200)

        anonymous_state = self.client.post(
            "/api/conversations/state",
            json={"credentialEnvelope": None},
        )
        self.assertEqual(anonymous_state.status_code, 200)
        self.assertEqual(anonymous_state.json(), {"sessions": [], "activeSessionId": None})

        relogin_session_secret = self.client.cookies.get(self.main.auth_service.session_cookie_name)
        self.assertIsNotNone(relogin_session_secret)
        relogin_envelope = self._issue_envelope(
            str(relogin_session_secret),
            github_user_id="1001",
            github_login="octocat",
            credential_id="cred-456",
        )

        restored = self.client.post(
            "/api/conversations/state",
            json={"credentialEnvelope": relogin_envelope},
        )
        self.assertEqual(restored.status_code, 200)
        payload = restored.json()
        self.assertEqual(payload["activeSessionId"], created_session_id)
        self.assertEqual(len(payload["sessions"]), 1)
        self.assertEqual(payload["sessions"][0]["id"], created_session_id)

    def test_authenticated_history_is_shared_across_clients_for_same_user(self) -> None:
        first_session_secret = self._establish_browser_session(self.client)
        first_envelope = self._issue_envelope(
            first_session_secret,
            github_user_id="1001",
            github_login="octocat",
        )
        created = self.client.post(
            "/api/conversations",
            json={"model": "gpt-5.4", "credentialEnvelope": first_envelope},
        )
        self.assertEqual(created.status_code, 200)
        created_session_id = created.json()["session"]["id"]

        second_client = TestClient(self.main.app)
        try:
            second_session_secret = self._establish_browser_session(second_client)
            second_envelope = self._issue_envelope(
                second_session_secret,
                github_user_id="1001",
                github_login="octocat",
                credential_id="cred-789",
            )
            restored = second_client.post(
                "/api/conversations/state",
                json={"credentialEnvelope": second_envelope},
            )
        finally:
            second_client.close()

        self.assertEqual(restored.status_code, 200)
        payload = restored.json()
        self.assertEqual(payload["activeSessionId"], created_session_id)
        self.assertEqual(len(payload["sessions"]), 1)
        self.assertEqual(payload["sessions"][0]["id"], created_session_id)

    def test_anonymous_history_is_not_merged_into_authenticated_history(self) -> None:
        self._establish_browser_session()
        anonymous_created = self.client.post(
            "/api/conversations",
            json={"model": "gpt-5.4"},
        )
        self.assertEqual(anonymous_created.status_code, 200)

        session_secret = self.client.cookies.get(self.main.auth_service.session_cookie_name)
        authenticated_envelope = self._issue_envelope(
            str(session_secret),
            github_user_id="2002",
            github_login="hubot",
        )

        authenticated_state = self.client.post(
            "/api/conversations/state",
            json={"credentialEnvelope": authenticated_envelope},
        )
        self.assertEqual(authenticated_state.status_code, 200)
        self.assertEqual(authenticated_state.json(), {"sessions": [], "activeSessionId": None})

        anonymous_state = self.client.get("/api/conversations")
        self.assertEqual(anonymous_state.status_code, 200)
        self.assertEqual(len(anonymous_state.json()["sessions"]), 1)


class UserScopedHistoryRetentionTests(unittest.TestCase):
    def test_conversations_older_than_seven_days_are_purged(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            db_path = Path(temp_dir) / "chat-history.sqlite3"
            service = ConversationService(
                history_db_path=db_path,
                ttl_seconds=60 * 60 * 24 * 7,
                cleanup_interval_seconds=0,
            )
            stale = service.create_conversation("user:1001", "gpt-5.4")
            fresh = service.create_conversation("user:1001", "gpt-5.4")
            cutoff_time = time.time() - (60 * 60 * 24 * 7) - 10

            connection = sqlite3.connect(db_path)
            try:
                connection.execute(
                    "UPDATE conversations SET updated_at = ? WHERE id = ?",
                    (cutoff_time, stale["id"]),
                )
                connection.commit()
            finally:
                connection.close()

            payload = service.get_state_payload("user:1001")

        self.assertEqual([session["id"] for session in payload["sessions"]], [fresh["id"]])
        self.assertEqual(payload["activeSessionId"], fresh["id"])


if __name__ == "__main__":
    unittest.main()
