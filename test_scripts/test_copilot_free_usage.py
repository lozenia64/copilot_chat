from __future__ import annotations

from _path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx

from services.copilot_auth import CopilotAuthService


class CopilotFreeUsageTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_fetch_usage_snapshot_normalizes_free_limited_user_shape(self) -> None:
        self.service.github_usage_urls = ("https://api.github.com/copilot_internal/user",)

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
                        "login": "lozenia642",
                        "access_type_sku": "free_limited_copilot",
                        "copilot_plan": "individual",
                        "limited_user_quotas": {
                            "chat": 480,
                            "completions": 4000,
                        },
                        "monthly_quotas": {
                            "chat": 500,
                            "completions": 4000,
                        },
                        "limited_user_reset_date": "2026-06-07",
                    },
                    request=httpx.Request("GET", url),
                )

        with patch("services.copilot_auth.httpx.AsyncClient", FakeAsyncClient):
            snapshot = await self.service.fetch_usage_snapshot("github-access-token")

        self.assertEqual(snapshot["status"], "partial")
        self.assertEqual(snapshot["reason"], "copilot_usage_partial")
        self.assertEqual(
            snapshot["detail"],
            "GitHub Copilot 사용량 정보 일부만 확인되었습니다.",
        )
        self.assertEqual(snapshot["source"], "copilot_user_api")
        self.assertEqual(snapshot["accessTypeSku"], "free_limited_copilot")
        self.assertEqual(snapshot["chatMessages"]["remaining"], 480)
        self.assertEqual(snapshot["chatMessages"]["used"], 20)
        self.assertEqual(snapshot["chatMessages"]["total"], 500)
        self.assertEqual(snapshot["chatMessages"]["plan"], "individual")
        self.assertFalse(snapshot["chatMessages"]["unlimited"])
        self.assertEqual(snapshot["chatMessages"]["status"], "available")
        self.assertEqual(snapshot["premiumRequests"]["status"], "missing")
        self.assertIsNone(snapshot["premiumRequests"]["remaining"])
        self.assertIsNone(snapshot["premiumRequests"]["total"])

    async def test_fetch_usage_snapshot_normalizes_paid_quota_snapshots_shape(self) -> None:
        self.service.github_usage_urls = ("https://api.github.com/copilot_internal/user",)

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
                        "login": "lozenia64",
                        "access_type_sku": "monthly_subscriber_quota",
                        "copilot_plan": "individual",
                        "quota_snapshots": {
                            "chat": {
                                "remaining": 0,
                                "entitlement": 0,
                                "unlimited": True,
                                "has_quota": False,
                            },
                            "premium_interactions": {
                                "remaining": 237,
                                "entitlement": 300,
                                "unlimited": False,
                            },
                        },
                    },
                    request=httpx.Request("GET", url),
                )

        with patch("services.copilot_auth.httpx.AsyncClient", FakeAsyncClient):
            snapshot = await self.service.fetch_usage_snapshot("github-access-token")

        self.assertEqual(snapshot["status"], "ok")
        self.assertEqual(snapshot["reason"], "copilot_usage_ok")
        self.assertEqual(snapshot["source"], "copilot_user_api")
        self.assertEqual(snapshot["accessTypeSku"], "monthly_subscriber_quota")
        self.assertTrue(snapshot["chatMessages"]["unlimited"])
        self.assertEqual(snapshot["chatMessages"]["status"], "available")
        self.assertIsNone(snapshot["chatMessages"]["remaining"])
        self.assertEqual(snapshot["premiumRequests"]["remaining"], 237)
        self.assertEqual(snapshot["premiumRequests"]["used"], 63)
        self.assertEqual(snapshot["premiumRequests"]["total"], 300)
        self.assertFalse(snapshot["premiumRequests"]["unlimited"])
        self.assertEqual(snapshot["premiumRequests"]["status"], "available")


if __name__ == "__main__":
    unittest.main()
