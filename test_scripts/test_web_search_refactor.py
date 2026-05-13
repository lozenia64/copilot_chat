from __future__ import annotations

from _path_setup import ensure_project_root_on_path

ensure_project_root_on_path()

import unittest
from pathlib import Path
from unittest.mock import AsyncMock, patch

import httpx

from services.copilot_auth import CopilotCredentialSession
from services.copilot_chat import CopilotChatService
from services.web_search import WebSearchClient, WebSearchResult


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


class WebSearchRefactorTests(unittest.IsolatedAsyncioTestCase):
    async def test_auto_explicit_search_uses_single_streaming_completion_call(self) -> None:
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
        fake_stream = FakeStream([{"choices": [{"delta": {"content": "hello"}}]}])
        client_tools = [
            {
                "type": "function",
                "function": {
                    "name": "client_tool",
                    "description": "passthrough tool",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
        ]

        with patch("services.copilot_chat.litellm.acompletion", AsyncMock(return_value=fake_stream)) as acompletion:
            chunks = [
                chunk
                async for chunk in service.stream_chat_completion(
                    request=FakeRequest(),
                    model="gpt-5.4",
                    messages=[{"role": "user", "content": "서울 날씨를 검색해보고 알려줘"}],
                    session=make_credential_session(),
                    tools=client_tools,
                    search_mode="auto",
                )
            ]

        payload = b"".join(chunks).decode("utf-8")
        service.search_client.search.assert_awaited_once_with("서울 날씨")
        acompletion.assert_awaited_once()
        kwargs = acompletion.await_args.kwargs
        self.assertTrue(kwargs["stream"])
        self.assertEqual(kwargs["tools"], client_tools)
        self.assertEqual(kwargs["messages"][0]["role"], "system")
        self.assertEqual(kwargs["messages"][1]["role"], "assistant")
        self.assertIn("data: [DONE]", payload)
        self.assertTrue(fake_stream.closed)

    async def test_auto_mode_without_explicit_search_skips_web_search(self) -> None:
        service = CopilotChatService(
            config_path=Path(__file__).resolve().parent / "litellm_config.yaml",
            default_model="gpt-5.4",
        )
        service.search_client.search = AsyncMock(return_value=[])
        original_messages = [{"role": "user", "content": "latest weather in Seoul"}]
        fake_stream = FakeStream([{"choices": [{"delta": {"content": "ordinary"}}]}])

        with patch("services.copilot_chat.litellm.acompletion", AsyncMock(return_value=fake_stream)) as acompletion:
            _ = [
                chunk
                async for chunk in service.stream_chat_completion(
                    request=FakeRequest(),
                    model="gpt-5.4",
                    messages=original_messages,
                    session=make_credential_session(),
                    search_mode="auto",
                )
            ]

        service.search_client.search.assert_not_awaited()
        self.assertEqual(acompletion.await_args.kwargs["messages"], original_messages)

    async def test_auto_mode_search_failure_falls_back_to_ordinary_chat_messages(self) -> None:
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
        original_messages = [{"role": "user", "content": "서울 날씨를 검색해보고 알려줘"}]
        fake_stream = FakeStream([{"choices": [{"delta": {"content": "ordinary"}}]}])

        with patch("services.copilot_chat.litellm.acompletion", AsyncMock(return_value=fake_stream)) as acompletion:
            chunks = [
                chunk
                async for chunk in service.stream_chat_completion(
                    request=FakeRequest(),
                    model="gpt-5.4",
                    messages=original_messages,
                    session=make_credential_session(),
                    search_mode="auto",
                )
            ]

        payload = b"".join(chunks).decode("utf-8")
        self.assertEqual(acompletion.await_args.kwargs["messages"], original_messages)
        self.assertIn("data: [DONE]", payload)

    async def test_web_search_client_disables_follow_redirects(self) -> None:
        client = WebSearchClient()
        captured_kwargs: dict[str, object] = {}

        class FakeResponse:
            text = ""

            def raise_for_status(self) -> None:
                return None

        class FakeAsyncClient:
            def __init__(self, **kwargs) -> None:
                captured_kwargs.update(kwargs)
                self.called_url = None

            async def __aenter__(self):
                return self

            async def __aexit__(self, exc_type, exc, tb) -> bool:
                return False

            async def get(self, url):
                self.called_url = url
                return FakeResponse()

        with patch("services.web_search.httpx.AsyncClient", FakeAsyncClient):
            await client.search("seoul weather")

        self.assertFalse(captured_kwargs["follow_redirects"])

    def test_web_search_client_drops_non_http_result_urls(self) -> None:
        client = WebSearchClient()
        html = """
        <a class='result-link' href='javascript:alert(1)'>Bad</a>
        <td class='result-snippet'>ignored</td>
        <a class='result-link' href='https://safe.example/path'>Good</a>
        <td class='result-snippet'>snippet</td>
        """

        results = client._parse(html)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].title, "Good")
        self.assertEqual(results[0].url, "https://safe.example/path")


if __name__ == "__main__":
    unittest.main()
