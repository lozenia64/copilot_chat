"""Tavily 기반 웹 검색 클라이언트.

TAVILY_API_KEY 환경변수에서 API 키를 읽는다. 키가 없거나 API 호출이 실패해도
예외를 raise 하지 않고 빈 리스트를 반환하므로 호출자(copilot_chat.py)의 도구
호출 루프는 중단 없이 모델에게 실패 사실을 전달하고 계속 진행된다.

Tavily 응답 구조:
  {
    "results": [
      {"title": "...", "url": "...", "content": "...(snippet)...", "score": 0.9},
      ...
    ]
  }
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import asdict, dataclass
from typing import Any
from urllib.parse import urlsplit


LOGGER = logging.getLogger(__name__)

WEB_SEARCH_TOOL_NAME = "web_search"
ALLOWED_SOURCE_URL_SCHEMES = {"http", "https"}


def normalize_source_url(value: Any) -> str:
    if not isinstance(value, str):
        return ""

    normalized_value = value.strip()
    if not normalized_value:
        return ""

    parsed = urlsplit(normalized_value)
    if parsed.scheme.lower() not in ALLOWED_SOURCE_URL_SCHEMES or not parsed.netloc:
        return ""

    return normalized_value


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str = ""


class WebSearchClient:
    """Tavily API 기반 웹 검색 클라이언트.

    - TAVILY_API_KEY 환경변수가 없으면 클라이언트를 초기화하지 않고
      search() 호출 시 빈 리스트를 반환한다.
    - API 오류(InvalidAPIKeyError, UsageLimitExceededError, 네트워크 오류 등)
      모두 빈 리스트로 처리하여 스트림을 중단시키지 않는다.
    """

    def __init__(self, *, max_results: int = 5) -> None:
        self.max_results = max_results
        self._client: Any = None

        api_key = os.getenv("TAVILY_API_KEY")
        if not api_key:
            LOGGER.warning(
                "TAVILY_API_KEY is not set; web search will always return empty results."
            )
            return

        try:
            from tavily import AsyncTavilyClient  # type: ignore[import]

            self._client = AsyncTavilyClient(api_key=api_key)
            LOGGER.info("Tavily AsyncTavilyClient initialized successfully.")
        except Exception as exc:
            LOGGER.warning("Failed to initialize Tavily client: %s: %s", type(exc).__name__, exc)

    async def search(self, query: str) -> list[WebSearchResult]:
        normalized_query = query.strip()
        if not normalized_query:
            return []

        if self._client is None:
            LOGGER.warning("Tavily client is unavailable; skipping web search.")
            return []

        try:
            response = await self._client.search(
                query=normalized_query,
                max_results=self.max_results,
                search_depth="basic",
            )
        except Exception as exc:
            LOGGER.warning(
                "Tavily search failed (%s); returning empty results.",
                type(exc).__name__,
            )
            return []

        raw_results = response.get("results") if isinstance(response, dict) else []
        if not isinstance(raw_results, list):
            LOGGER.warning("Unexpected Tavily response shape; 'results' is not a list.")
            return []

        results: list[WebSearchResult] = []
        seen_urls: set[str] = set()
        for item in raw_results:
            if not isinstance(item, dict):
                continue
            title = (item.get("title") or "").strip()
            url = normalize_source_url(item.get("url"))
            snippet = (item.get("content") or "").strip()
            if not title or not url or url in seen_urls:
                continue
            results.append(WebSearchResult(title=title, url=url, snippet=snippet))
            seen_urls.add(url)

        LOGGER.info("Tavily search returned %d result(s).", len(results))
        return results


def extract_result_sources(results: list[WebSearchResult]) -> list[dict[str, str]]:
    sources: list[dict[str, str]] = []
    seen_urls: set[str] = set()
    for result in results:
        title = result.title.strip()
        url = normalize_source_url(result.url)
        if not title or not url or url in seen_urls:
            continue
        sources.append({"title": title, "url": url})
        seen_urls.add(url)
    return sources


def format_tool_result_content(query: str, results: list[WebSearchResult]) -> str:
    """`web_search` tool 호출 결과를 OpenAI tool 메시지 content 로 직렬화.

    결과가 비어 있으면 모델이 사용자에게 정직하게 알릴 수 있도록 명확한 note
    를 함께 첨부한다.
    """
    payload: dict[str, Any] = {
        "query": query,
        "result_count": len(results),
        "results": [asdict(result) for result in results],
    }
    if not results:
        payload["note"] = (
            "The web search returned no results. Answer from your own "
            "knowledge and tell the user that the live web search is currently "
            "unavailable."
        )
    return json.dumps(payload, ensure_ascii=False)
