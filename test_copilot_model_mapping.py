from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from services.copilot_auth import CopilotCredentialSession
from services.copilot_chat import CopilotChatService


def make_credential_session() -> CopilotCredentialSession:
    return CopilotCredentialSession(
        github_access_token="github-access-token",
        copilot_api_token="copilot-api-token",
        copilot_api_expires_at=9999999999.0,
        copilot_api_base="https://api.githubcopilot.com",
        issued_at=0.0,
        updated_at=0.0,
        credential_id="cred-123",
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


class CopilotModelMappingTests(unittest.IsolatedAsyncioTestCase):
    async def test_stream_uses_litellm_provider_model_mapping(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )

        with patch(
            "services.copilot_chat.litellm.acompletion",
            AsyncMock(return_value=FakeStream()),
        ) as acompletion:
            chunks = [
                chunk
                async for chunk in service.stream_chat_completion(
                    request=FakeRequest(),
                    model="gpt-5.4",
                    messages=[{"role": "user", "content": "hello"}],
                    session=make_credential_session(),
                )
            ]

        self.assertTrue(b"".join(chunks).decode("utf-8").rstrip().endswith("data: [DONE]"))
        self.assertEqual(acompletion.await_args.kwargs["model"], "github_copilot/gpt-5.4")

    async def test_ui_model_ids_stay_separate_from_provider_model_ids(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )

        self.assertEqual(
            service.get_models_payload(),
            {
                "data": [
                    {"id": "gpt-5.4"},
                    {"id": "gpt-5.5"},
                    {"id": "claude-haiku-4.5"},
                    {"id": "claude-sonnet-4.6"},
                    {"id": "claude-opus-4.7"},
                    {"id": "gemini-3.1-pro-preview"},
                ]
            },
        )
        self.assertEqual(
            service.resolve_litellm_model("gemini-3.1-pro-preview"),
            "github_copilot/gemini-3.1-pro-preview",
        )

    async def test_default_model_without_explicit_mapping_uses_own_id(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            config_path = Path(temp_dir) / "litellm_config.yaml"
            config_path.write_text(
                (
                    "model_list:\n"
                    "  - model_name: claude-haiku-4.5\n"
                    "    litellm_params:\n"
                    "      model: github_copilot/claude-haiku-4.5\n"
                ),
                encoding="utf-8",
            )
            service = CopilotChatService(
                config_path=config_path,
                default_model="gpt-5.4",
            )

        self.assertEqual(service.model_ids[0], "gpt-5.4")
        self.assertEqual(service.resolve_litellm_model("gpt-5.4"), "gpt-5.4")


if __name__ == "__main__":
    unittest.main()
