import unittest
from unittest.mock import AsyncMock, patch

from fastapi.testclient import TestClient

import main
from services.copilot_auth import CopilotAuthError


class ImageUploadApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client = TestClient(main.app)

    @classmethod
    def tearDownClass(cls):
        cls.client.close()

    def test_upload_request_validation_fails_when_credential_envelope_is_missing(self):
        response = self.client.post(
            "/api/uploads/images",
            data={"conversationId": "conv_test"},
            files={"file": ("sample.jpg", b"jpeg-bytes", "image/jpeg")},
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(response.json()["code"], "request_validation_failed")

    def test_upload_rejects_invalid_credential_envelope(self):
        with patch.object(main.auth_service, "get_or_create_session_secret", return_value=("session-secret", False)):
            with patch.object(
                main.auth_service,
                "resolve_session",
                new=AsyncMock(
                    side_effect=CopilotAuthError(
                        status_code=401,
                        message="이 브라우저의 GitHub Copilot 자격 정보가 없습니다. 먼저 로그인하세요.",
                        code="copilot_login_required",
                    )
                ),
            ):
                response = self.client.post(
                    "/api/uploads/images",
                    data={"conversationId": "conv_test", "credentialEnvelope": "invalid-envelope"},
                    files={"file": ("sample.jpg", b"jpeg-bytes", "image/jpeg")},
                )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "copilot_login_required")

    def test_delete_requires_credential_envelope(self):
        response = self.client.request(
            "DELETE",
            "/api/uploads/images/att_test",
            json={"conversationId": "conv_test"},
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["code"], "copilot_login_required")

    def test_content_requires_valid_signed_token(self):
        response = self.client.get(
            "/api/conversations/conv_test/attachments/att_test/content",
            params={"token": "invalid"},
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(response.json()["code"], "attachment_access_denied")


if __name__ == "__main__":
    unittest.main()