"""DuckDuckGo 기반 웹 검색 클라이언트.

이 모듈은 두 가지 관점에서 robust 하게 설계되었다.

1. **마크업 변종에 강건한 파서.**
   DuckDuckGo lite (`lite.duckduckgo.com/lite/`) 의 결과 anchor 는 일반적으로
       <a rel="nofollow" href="..." class='result-link'>제목</a>
   형태로 `class` 가 작은따옴표를 쓰고 `href` 가 `class` 앞에 온다. 반면 html
   엔드포인트 (`html.duckduckgo.com/html/`) 는
       <a class="result__a" href="...">제목</a>
   형태로 큰따옴표 + class 가 href 앞에 오는 경우가 많다. 단일 정규식으로
   둘을 모두 잡으려 하면 따옴표 종류·속성 순서 어느 하나에 의존하게 되어
   파서가 0개를 반환하는 결함이 쉽게 생긴다. 이 모듈은 a 태그 전체를 매칭한
   뒤 attrs 문자열에서 `class` 토큰과 `href` 값을 별도 정규식으로 추출한다.

2. **봇 차단(challenge) 응답 감지.**
   DuckDuckGo 는 봇으로 분류된 IP/클라이언트에 HTTP 202 + `anomaly.js?...cc=botnet`
   페이지를 돌려주는데, 이걸 "결과 없음" 으로 오해하면 호출자에게 misleading
   결과가 흘러간다. 본 모듈은 응답 상태와 본문 마커로 challenge 응답을 명시적
   으로 감지하고 그 경우 빈 결과로 처리하여 fallback 엔진(html 엔드포인트)으로
   넘긴다.

추가로 연속 호출은 1초 cooldown 으로 throttle 하여 봇 점수 누적을 막는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import asdict, dataclass
from html import unescape
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx


LOGGER = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# 파서: a 태그 → attrs 에서 class/href 별도 추출
# ---------------------------------------------------------------------------

_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")

_DDG_A_TAG_PATTERN = re.compile(
    r"<a\b(?P<attrs>[^>]*)>(?P<title>.*?)</a>",
    re.IGNORECASE | re.DOTALL,
)
_DDG_RESULT_LINK_CLASS_PATTERN = re.compile(
    r"""\bclass=["'][^"']*\b(?:result-link|result__a)\b[^"']*["']""",
    re.IGNORECASE,
)
_DDG_HREF_PATTERN = re.compile(r"""\bhref=["']([^"']+)["']""", re.IGNORECASE)
_DDG_SNIPPET_PATTERN = re.compile(
    r"<(?P<tag>div|a|td|span|p)\b[^>]*"
    r"""\bclass=["'][^"']*\b(?:result-snippet|result__snippet)\b[^"']*["']"""
    r"[^>]*>(?P<snippet>.*?)</(?P=tag)>",
    re.IGNORECASE | re.DOTALL,
)


# ---------------------------------------------------------------------------
# 봇 차단(challenge) 응답 감지 마커
# ---------------------------------------------------------------------------

_DDG_CHALLENGE_MARKERS = (
    "anomaly.js?sv=html",
    "cc=botnet",
    'id="challenge-form"',
    "js-anomaly-modal",
)


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str = ""


@dataclass(frozen=True)
class _SearchEngine:
    name: str
    url: str
    parser: Callable[[str], list[WebSearchResult]]


WEB_SEARCH_TOOL_NAME = "web_search"


class WebSearchClient:
    """DuckDuckGo lite/html 폴백 + cooldown 을 지원하는 웹 검색 클라이언트.

    - lite 가 첫 번째 우선순위. GET 응답 자체에 결과 페이지가 들어 있으며,
      vqd 토큰 기반 POST 단계는 필요하지 않다.
    - lite 가 challenge 페이지를 돌려주면 html 엔드포인트로 폴백.
    - 둘 다 실패하면 빈 리스트. 예외는 raise 하지 않고 호출자가
      `format_tool_result_content` 의 명확한 note 와 함께 모델에 전달한다.
    """

    MIN_INTERVAL_SECONDS = 1.0

    def __init__(self, *, max_results: int = 5) -> None:
        self.max_results = max_results
        self.engines: tuple[_SearchEngine, ...] = (
            _SearchEngine("duckduckgo_lite", "https://lite.duckduckgo.com/lite/", _parse_duckduckgo),
            _SearchEngine("duckduckgo_html", "https://html.duckduckgo.com/html/", _parse_duckduckgo),
        )
        self.timeout = httpx.Timeout(connect=5.0, read=10.0, write=8.0, pool=8.0)
        self.headers = {
            # 단순 폼 클라이언트 헤더 세트. Sec-Ch-Ua / Sec-Fetch-* 같은 Chrome 전용
            # client hints 를 함께 보내면 Python httpx 의 TLS 지문과 mismatch 가
            # 발생해 봇 분류기가 즉시 발동한다. UA / Accept / Accept-Language 만으로
            # 운영하는 게 가장 안정적이다.
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/131.0.0.0 Safari/537.36"
            ),
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
        }
        self._last_call_monotonic: float | None = None
        self._cooldown_lock = asyncio.Lock()

    async def search(self, query: str) -> list[WebSearchResult]:
        normalized_query = _normalize_query(query)
        if not normalized_query:
            return []

        await self._apply_cooldown()

        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers=self.headers,
            follow_redirects=True,
        ) as client:
            for engine in self.engines:
                try:
                    response = await client.get(engine.url, params={"q": normalized_query})
                except httpx.HTTPError as exc:
                    LOGGER.info("Search engine %s request failed: %s", engine.name, type(exc).__name__)
                    continue

                if response.status_code != 200 or _looks_like_bot_challenge(response):
                    LOGGER.info(
                        "Search engine %s returned a non-result response (status=%s); falling back",
                        engine.name,
                        response.status_code,
                    )
                    continue

                results = engine.parser(response.text)
                if results:
                    LOGGER.info("Search engine %s returned %d result(s)", engine.name, len(results))
                    return results[: self.max_results]

                LOGGER.info("Search engine %s returned no parseable results", engine.name)

        return []

    async def _apply_cooldown(self) -> None:
        async with self._cooldown_lock:
            if self._last_call_monotonic is not None:
                elapsed = time.monotonic() - self._last_call_monotonic
                wait = self.MIN_INTERVAL_SECONDS - elapsed
                if wait > 0:
                    LOGGER.info("Web search cooldown: sleeping %.2fs", wait)
                    await asyncio.sleep(wait)
            self._last_call_monotonic = time.monotonic()


def _looks_like_bot_challenge(response: Any) -> bool:
    if response.status_code == 202:
        return True
    body = response.text
    return any(marker in body for marker in _DDG_CHALLENGE_MARKERS)


def _parse_duckduckgo(document: str) -> list[WebSearchResult]:
    if not document:
        return []

    ordered_links: list[tuple[int, str, str]] = []
    for match in _DDG_A_TAG_PATTERN.finditer(document):
        attrs = match.group("attrs")
        if not _DDG_RESULT_LINK_CLASS_PATTERN.search(attrs):
            continue
        href_match = _DDG_HREF_PATTERN.search(attrs)
        if not href_match:
            continue
        ordered_links.append((match.start(), href_match.group(1), match.group("title")))

    ordered_snippets = [
        snippet
        for _, snippet in sorted(
            (match.start(), _clean_html_text(match.group("snippet")))
            for match in _DDG_SNIPPET_PATTERN.finditer(document)
        )
        if snippet
    ]

    results: list[WebSearchResult] = []
    seen_urls: set[str] = set()
    for index, (_, raw_href, raw_title) in enumerate(ordered_links):
        title = _clean_html_text(raw_title)
        url = _normalize_duckduckgo_url(raw_href)
        if not title or not url or url in seen_urls:
            continue

        snippet = ordered_snippets[index] if index < len(ordered_snippets) else ""
        results.append(WebSearchResult(title=title, url=url, snippet=snippet))
        seen_urls.add(url)

    return results


def _normalize_query(query: str) -> str:
    return _WHITESPACE_PATTERN.sub(" ", query).strip()


def _normalize_duckduckgo_url(href: str) -> str | None:
    """DuckDuckGo 의 redirect anchor (`//duckduckgo.com/l/?uddg=<encoded>&...`)
    에서 실제 대상 URL 을 추출한다."""
    candidate = unescape(href).strip()
    if not candidate:
        return None

    if candidate.startswith("//"):
        candidate = f"https:{candidate}"

    resolved = urljoin("https://duckduckgo.com", candidate)
    parsed = urlparse(resolved)

    redirected_url = parse_qs(parsed.query).get("uddg", [None])[0]
    if redirected_url:
        return unquote(redirected_url)

    if parsed.scheme not in {"http", "https"}:
        return None
    if parsed.netloc.endswith("duckduckgo.com"):
        return None
    return parsed.geturl()


def _clean_html_text(fragment: str) -> str:
    text = unescape(_TAG_PATTERN.sub(" ", fragment))
    return _WHITESPACE_PATTERN.sub(" ", text).strip()


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
            "All upstream search engines either returned no parseable results "
            "or responded with anti-bot challenge pages. Answer from your own "
            "knowledge and tell the user that the live web search is currently "
            "unavailable."
        )
    return json.dumps(payload, ensure_ascii=False)
