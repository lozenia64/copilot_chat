from __future__ import annotations

from _path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

from services.copilot_auth import CopilotCredentialSession
from services.copilot_chat import CopilotChatService
from services.conversation_service import ConversationService


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


class FakeCompletionStream:
    def __init__(self, chunks: list[dict[str, object]], *, error: Exception | None = None) -> None:
        self._chunks = chunks
        self._index = 0
        self._error = error

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._chunks):
            if self._error is not None:
                error = self._error
                self._error = None
                raise error
            raise StopAsyncIteration
        chunk = self._chunks[self._index]
        self._index += 1
        return chunk

    async def aclose(self) -> None:
        return None


class CopilotChatStreamErrorTests(unittest.IsolatedAsyncioTestCase):
    async def test_successful_stream_passthroughs_delta_and_done(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )
        fake_stream = FakeCompletionStream(
            [
                {"choices": [{"delta": {"content": "hello"}}]},
                {"choices": [{"delta": {"content": " world"}}]},
            ]
        )

        with patch(
            "services.copilot_chat.litellm.acompletion",
            AsyncMock(return_value=fake_stream),
        ):
            chunks = []
            stream = service.stream_chat_completion(
                request=FakeRequest(),
                model="gpt-5.4",
                messages=[{"role": "user", "content": "hello"}],
                session=make_credential_session(),
            )

            async for chunk in stream:
                chunks.append(chunk)

        payload = b"".join(chunks).decode("utf-8")
        self.assertIn('"content": "hello"', payload)
        self.assertIn('"content": " world"', payload)
        self.assertTrue(payload.rstrip().endswith("data: [DONE]"))

    async def test_model_not_supported_returns_specific_sse_error(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )

        with patch("services.copilot_chat.LOGGER.warning") as logger_warning:
            with patch(
                "services.copilot_chat.litellm.acompletion",
                AsyncMock(
                    side_effect=RuntimeError(
                        "litellm.BadRequestError: OpenAIException - The requested model is not supported."
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
        self.assertIn(
            'data: {"code": "copilot_model_not_supported", "message": "현재 로그인 되어있는 GitHub 계정에서는 해당 모델을 사용할 수 없습니다."}',
            payload,
        )
        self.assertTrue(payload.rstrip().endswith("data: [DONE]"))
        logger_warning.assert_called_once()
        self.assertEqual(
            logger_warning.call_args.args,
            (
                "Copilot chat streaming failed; returning sanitized SSE error (%s)",
                "copilot_model_not_supported",
            ),
        )
        self.assertEqual(logger_warning.call_args.kwargs, {})

    async def test_partial_delta_then_stream_error_still_ends_with_done(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )
        fake_stream = FakeCompletionStream(
            [{"choices": [{"delta": {"content": "partial answer"}}]}],
            error=RuntimeError(
                "litellm.APIError: OpenAIException - upstream failure while streaming"
            ),
        )

        with patch("services.copilot_chat.LOGGER.warning") as logger_warning:
            with patch(
                "services.copilot_chat.litellm.acompletion",
                AsyncMock(return_value=fake_stream),
            ):
                chunks = []
                stream = service.stream_chat_completion(
                    request=FakeRequest(),
                    model="gpt-5.4",
                    messages=[{"role": "user", "content": "hello"}],
                    session=make_credential_session(),
                )

                async for chunk in stream:
                    chunks.append(chunk)

        payload = b"".join(chunks).decode("utf-8")
        self.assertIn('"content": "partial answer"', payload)
        self.assertIn('"code": "copilot_chat_stream_failed"', payload)
        self.assertTrue(payload.rstrip().endswith("data: [DONE]"))
        self.assertLess(payload.index('"content": "partial answer"'), payload.index('"code": "copilot_chat_stream_failed"'))
        logger_warning.assert_called_once()


class ConversationServiceStreamErrorRecognitionTests(unittest.TestCase):
    def test_model_not_supported_is_treated_as_terminal_stream_error(self) -> None:
        service = ConversationService()
        self.assertTrue(
            service._is_stream_error_payload({"code": "copilot_model_not_supported"})
        )


if __name__ == "__main__":
    unittest.main()
