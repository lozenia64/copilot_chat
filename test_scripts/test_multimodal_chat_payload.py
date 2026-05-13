from _path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest import mock

from PIL import Image

from services.copilot_auth import CopilotCredentialSession
from services.copilot_chat import CopilotChatRequestError, CopilotChatService
from services.web_search import WebSearchClient, WebSearchResult


ROOT_DIR = Path(__file__).resolve().parents[1]


class FakeStream:
    def __init__(self, chunks):
        self._chunks = list(chunks)
        self._iterator = iter(())

    def __aiter__(self):
        self._iterator = iter(self._chunks)
        return self

    async def __anext__(self):
        try:
            return next(self._iterator)
        except StopIteration as exc:
            raise StopAsyncIteration from exc

    async def aclose(self):
        return None


class ConnectedRequest:
    async def is_disconnected(self):
        return False


class MultimodalChatPayloadTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)
        self.image_path = Path(self.temp_dir.name) / "sample.jpg"
        image = Image.new("RGB", (8, 8), color=(255, 0, 0))
        image.save(self.image_path, format="JPEG")
        self.service = CopilotChatService(
            config_path=ROOT_DIR / "litellm_config.yaml",
            default_model="gpt-5.4",
        )

    def test_build_provider_messages_summarizes_history_and_embeds_current_image(self):
        provider_messages = self.service.build_provider_messages(
            [
                {
                    "role": "user",
                    "content": "이전 질문",
                    "attachments": [{"fileName": "older.jpg"}],
                },
                {"role": "assistant", "content": "이전 답변"},
            ],
            {
                "content": "이 이미지를 설명해줘",
                "attachments": [{"fileName": "fresh.jpg", "storagePath": str(self.image_path)}],
            },
        )

        self.assertEqual(
            provider_messages[0]["content"],
            "이전 질문\n\n[사용자가 이미지 1개를 첨부함: older.jpg]",
        )
        self.assertEqual(provider_messages[1], {"role": "assistant", "content": "이전 답변"})
        self.assertEqual(provider_messages[2]["role"], "user")
        self.assertEqual(provider_messages[2]["content"][0], {"type": "text", "text": "이 이미지를 설명해줘"})
        self.assertEqual(provider_messages[2]["content"][1]["type"], "image_url")
        self.assertTrue(
            provider_messages[2]["content"][1]["image_url"]["url"].startswith("data:image/jpeg;base64,")
        )

    @mock.patch("services.copilot_chat.litellm.supports_vision", return_value=False)
    def test_ensure_model_supports_vision_rejects_nonvision_model(self, _mock_supports_vision):
        with self.assertRaises(CopilotChatRequestError) as raised:
            self.service.ensure_model_supports_vision("gpt-5.4")

        self.assertEqual(raised.exception.code, "chat_model_not_vision_capable")


class ChatStreamSourceMetadataTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.service = CopilotChatService(
            config_path=ROOT_DIR / "litellm_config.yaml",
            default_model="gpt-5.4",
        )

    async def test_stream_chat_completion_emits_assistant_sources_metadata(self):
        self.service.search_client.search = mock.AsyncMock(
            return_value=[
                WebSearchResult(title="첫 기사", url="https://example.com/a", snippet=""),
                WebSearchResult(title="위험 기사", url="javascript:alert(1)", snippet=""),
                WebSearchResult(title="FTP 기사", url="ftp://example.com/file", snippet=""),
                WebSearchResult(title="중복 기사", url="https://example.com/a", snippet=""),
                WebSearchResult(title="둘째 기사", url="http://example.com/b", snippet=""),
            ]
        )
        session = CopilotCredentialSession(
            github_access_token="ghu_test",
            copilot_api_token="copilot_test",
            copilot_api_expires_at=0,
            copilot_api_base="https://api.githubcopilot.com",
            issued_at=0,
            updated_at=0,
            credential_id="cred_test",
        )
        first_stream = FakeStream(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "call_1",
                                        "type": "function",
                                        "function": {
                                            "name": "web_search",
                                            "arguments": '{"query":"최신 AI 뉴스"}',
                                        },
                                    }
                                ]
                            }
                        }
                    ]
                }
            ]
        )
        second_stream = FakeStream(
            [
                {
                    "choices": [
                        {
                            "delta": {
                                "content": "최종 답변",
                            }
                        }
                    ]
                }
            ]
        )

        with mock.patch(
            "services.copilot_chat.litellm.acompletion",
            new=mock.AsyncMock(side_effect=[first_stream, second_stream]),
        ):
            chunks: list[str] = []
            async for chunk in self.service.stream_chat_completion(
                request=ConnectedRequest(),
                model="gpt-5.4",
                messages=[{"role": "user", "content": "질문"}],
                session=session,
                initiator_messages=[{"role": "user", "content": "질문"}],
                tools=[{"type": "function", "function": {"name": "web_search"}}],
                tool_choice="auto",
                parallel_tool_calls=False,
            ):
                chunks.append(chunk.decode("utf-8").strip())

        payloads: list[dict | str] = []
        for chunk in chunks:
            if chunk == "data: [DONE]":
                payloads.append("[DONE]")
                continue
            self.assertTrue(chunk.startswith("data: "))
            payloads.append(json.loads(chunk[6:]))

        assistant_sources_payloads = [
            payload
            for payload in payloads
            if isinstance(payload, dict) and payload.get("type") == "assistant_sources"
        ]
        self.assertEqual(
            assistant_sources_payloads,
            [
                {
                    "type": "assistant_sources",
                    "sources": [
                        {"title": "첫 기사", "url": "https://example.com/a"},
                        {"title": "둘째 기사", "url": "http://example.com/b"},
                    ],
                }
            ],
        )
        self.assertEqual(payloads[-1], "[DONE]")
        visible_payloads = [
            json.dumps(payload, ensure_ascii=False)
            for payload in payloads
            if isinstance(payload, dict)
        ]
        self.assertFalse(any("tool_calls" in payload for payload in visible_payloads))

    def test_tool_status_payload_hides_raw_search_query(self):
        payload = self.service._tool_status_payload(
            {
                "id": "call_1",
                "type": "function",
                "function": {
                    "name": "web_search",
                    "arguments": '{"query":"최신 AI 뉴스"}',
                },
            }
        )

        self.assertEqual(
            payload,
            {"type": "assistant_status", "status": "web_searching"},
        )
        self.assertNotIn("최신 AI 뉴스", json.dumps(payload, ensure_ascii=False))


class WebSearchLoggingTests(unittest.IsolatedAsyncioTestCase):
    async def test_search_unavailable_log_redacts_raw_query(self):
        client = WebSearchClient()
        client._client = None
        sensitive_query = "민감한 내부 검색어"

        with self.assertLogs("services.web_search", level="WARNING") as captured:
            results = await client.search(sensitive_query)

        self.assertEqual(results, [])
        self.assertNotIn(sensitive_query, "\n".join(captured.output))

    async def test_search_success_log_redacts_raw_query(self):
        client = WebSearchClient()
        client._client = mock.Mock()
        client._client.search = mock.AsyncMock(
            return_value={
                "results": [
                    {
                        "title": "첫 기사",
                        "url": "https://example.com/a",
                        "content": "요약",
                    }
                ]
            }
        )
        sensitive_query = "민감한 내부 검색어"

        with self.assertLogs("services.web_search", level="INFO") as captured:
            results = await client.search(sensitive_query)

        self.assertEqual(
            results,
            [WebSearchResult(title="첫 기사", url="https://example.com/a", snippet="요약")],
        )
        self.assertNotIn(sensitive_query, "\n".join(captured.output))

    async def test_search_provider_exception_log_redacts_raw_query(self):
        client = WebSearchClient()
        sensitive_query = "민감한 내부 검색어"
        client._client = mock.Mock()
        client._client.search = mock.AsyncMock(
            side_effect=RuntimeError(f"provider rejected query: {sensitive_query}")
        )

        with self.assertLogs("services.web_search", level="WARNING") as captured:
            results = await client.search(sensitive_query)

        self.assertEqual(results, [])
        joined_logs = "\n".join(captured.output)
        self.assertNotIn(sensitive_query, joined_logs)
        self.assertIn("RuntimeError", joined_logs)


if __name__ == "__main__":
    unittest.main()