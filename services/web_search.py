from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from html import unescape
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import httpx


LOGGER = logging.getLogger(__name__)

_TAG_PATTERN = re.compile(r"<[^>]+>")
_WHITESPACE_PATTERN = re.compile(r"\s+")
_LINK_PATTERNS = (
    re.compile(
        r'<a[^>]*class="[^"]*\bresult-link\b[^"]*"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r'<a[^>]*class="[^"]*\bresult__a\b[^"]*"[^>]*href="(?P<href>[^"]+)"[^>]*>(?P<title>.*?)</a>',
        re.IGNORECASE | re.DOTALL,
    ),
)
_SNIPPET_PATTERNS = (
    re.compile(
        r'<(?:div|a|td|span)[^>]*class="[^"]*\bresult-snippet\b[^"]*"[^>]*>(?P<snippet>.*?)</(?:div|a|td|span)>',
        re.IGNORECASE | re.DOTALL,
    ),
    re.compile(
        r'<(?:div|a|td|span)[^>]*class="[^"]*\bresult__snippet\b[^"]*"[^>]*>(?P<snippet>.*?)</(?:div|a|td|span)>',
        re.IGNORECASE | re.DOTALL,
    ),
)


@dataclass(frozen=True)
class WebSearchResult:
    title: str
    url: str
    snippet: str = ""


class DuckDuckGoSearchClient:
    def __init__(self, *, max_results: int = 5) -> None:
        self.max_results = max_results
        self.endpoints = (
            "https://lite.duckduckgo.com/lite/",
            "https://html.duckduckgo.com/html/",
        )
        self.timeout = httpx.Timeout(connect=5.0, read=8.0, write=8.0, pool=8.0)
        self.headers = {
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Accept-Language": "ko-KR,ko;q=0.9,en-US;q=0.7,en;q=0.6",
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/124.0.0.0 Safari/537.36"
            ),
        }

    async def search(self, query: str) -> list[WebSearchResult]:
        normalized_query = self._normalize_query(query)
        if not normalized_query:
            return []

        last_error: Exception | None = None
        async with httpx.AsyncClient(
            timeout=self.timeout,
            headers=self.headers,
            follow_redirects=True,
        ) as client:
            for endpoint in self.endpoints:
                try:
                    response = await client.get(endpoint, params={"q": normalized_query})
                    response.raise_for_status()
                except httpx.HTTPError as exc:
                    last_error = exc
                    continue

                results = self._parse_results(response.text)
                if results:
                    return results[: self.max_results]

        if last_error is not None:
            raise last_error

        LOGGER.info("Web search returned no parseable results")
        return []

    def _parse_results(self, document: str) -> list[WebSearchResult]:
        if not document:
            return []

        link_matches_by_position: dict[int, re.Match[str]] = {}
        for pattern in _LINK_PATTERNS:
            for match in pattern.finditer(document):
                link_matches_by_position.setdefault(match.start(), match)

        snippet_matches: dict[int, re.Match[str]] = {}
        for pattern in _SNIPPET_PATTERNS:
            for match in pattern.finditer(document):
                snippet_matches.setdefault(match.start(), match)

        ordered_snippets = [
            snippet
            for _, match in sorted(snippet_matches.items())
            if (snippet := self._clean_html_text(match.group("snippet")))
        ]

        results: list[WebSearchResult] = []
        seen_urls: set[str] = set()
        for index, (_, match) in enumerate(sorted(link_matches_by_position.items())):
            title = self._clean_html_text(match.group("title"))
            url = self._normalize_result_url(match.group("href"))
            if not title or not url or url in seen_urls:
                continue

            snippet = ordered_snippets[index] if index < len(ordered_snippets) else ""
            results.append(WebSearchResult(title=title, url=url, snippet=snippet))
            seen_urls.add(url)

            if len(results) >= self.max_results:
                break

        return results

    def _normalize_query(self, query: str) -> str:
        return _WHITESPACE_PATTERN.sub(" ", query).strip()

    def _normalize_result_url(self, href: str) -> str | None:
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

    def _clean_html_text(self, fragment: str) -> str:
        text = unescape(_TAG_PATTERN.sub(" ", fragment))
        return _WHITESPACE_PATTERN.sub(" ", text).strip()