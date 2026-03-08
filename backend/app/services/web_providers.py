"""Multi-provider web search with automatic fallback.

Provider chain (tried in priority order):
1. FirecrawlProvider  — full-featured search + scrape (FIRECRAWL_API_KEY)
2. GoogleSearchProvider — Google Custom Search JSON API (GOOGLE_CSE_API_KEY + GOOGLE_CSE_ID)
3. HttpxFallbackProvider — raw HTTP GET + html2text conversion (no API key needed, scrape-only)

MultiProviderSearch.from_env() auto-detects which providers are available
from environment variables (loaded by GCP Secret Manager at startup).
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

import httpx

from app.services.web_search import ScrapedPage, SearchResult

logger = logging.getLogger("nexsidi.web_providers")


# ── Provider Protocol ─────────────────────────────────────────

@runtime_checkable
class WebProvider(Protocol):
    """Interface every web provider must satisfy."""

    @property
    def name(self) -> str:
        """Human-readable provider name for logging."""
        ...

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Search the web, returning a list of results."""
        ...

    async def scrape(self, url: str) -> ScrapedPage | None:
        """Scrape a single URL, returning markdown content or None."""
        ...


# ── Firecrawl Provider ────────────────────────────────────────

class FirecrawlProvider:
    """Full-featured search + scrape via the Firecrawl API.

    Requires FIRECRAWL_API_KEY in the environment.
    """

    name: str = "firecrawl"

    def __init__(self) -> None:
        self._client: Any = None
        self._initialized = False

    def _ensure_init(self) -> bool:
        if self._initialized:
            return self._client is not None

        self._initialized = True
        api_key = os.environ.get("FIRECRAWL_API_KEY", "")
        if not api_key:
            logger.warning("firecrawl_provider: FIRECRAWL_API_KEY not set")
            return False

        try:
            from firecrawl import FirecrawlApp
            self._client = FirecrawlApp(api_key=api_key)
            logger.info("firecrawl_provider: initialized")
            return True
        except ImportError:
            logger.warning("firecrawl_provider: firecrawl-py not installed")
            return False
        except Exception as exc:
            logger.error("firecrawl_provider: init failed — %s", exc)
            return False

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not self._ensure_init():
            return []

        try:
            response = self._client.search(
                query=query,
                params={"limit": max_results},
            )
            data = response if isinstance(response, list) else response.get("data", [])
            results: list[SearchResult] = []
            for item in data[:max_results]:
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", item.get("snippet", "")),
                    content=item.get("markdown", item.get("content", "")),
                ))
            return results
        except Exception as exc:
            logger.error("firecrawl_search_failed: %s", exc)
            return []

    async def scrape(self, url: str) -> ScrapedPage | None:
        if not self._ensure_init():
            return None

        try:
            response = self._client.scrape_url(
                url=url,
                params={"formats": ["markdown"]},
            )
            data = response if isinstance(response, dict) else {}
            markdown = data.get("markdown", data.get("content", ""))
            metadata = data.get("metadata", {})
            return ScrapedPage(
                url=url,
                title=metadata.get("title", ""),
                markdown=markdown,
                metadata=metadata,
            )
        except Exception as exc:
            logger.error("firecrawl_scrape_failed: url=%s error=%s", url[:80], exc)
            return None


# ── Google Custom Search Provider ─────────────────────────────

class GoogleSearchProvider:
    """Web search via Google Custom Search JSON API.

    Requires GOOGLE_CSE_API_KEY and GOOGLE_CSE_ID in the environment.
    Endpoint: https://www.googleapis.com/customsearch/v1
    """

    name: str = "google_cse"

    _ENDPOINT = "https://www.googleapis.com/customsearch/v1"

    def __init__(self) -> None:
        self._api_key: str = ""
        self._cse_id: str = ""
        self._initialized = False

    def _ensure_init(self) -> bool:
        if self._initialized:
            return bool(self._api_key and self._cse_id)

        self._initialized = True
        self._api_key = os.environ.get("GOOGLE_CSE_API_KEY", "")
        self._cse_id = os.environ.get("GOOGLE_CSE_ID", "")

        if not self._api_key or not self._cse_id:
            logger.warning(
                "google_cse_provider: GOOGLE_CSE_API_KEY or GOOGLE_CSE_ID not set"
            )
            return False

        logger.info("google_cse_provider: initialized")
        return True

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        if not self._ensure_init():
            return []

        # Google CSE caps at 10 per request
        num = min(max_results, 10)

        try:
            async with httpx.AsyncClient(timeout=15.0) as client:
                resp = await client.get(
                    self._ENDPOINT,
                    params={
                        "key": self._api_key,
                        "cx": self._cse_id,
                        "q": query,
                        "num": num,
                    },
                )
                resp.raise_for_status()

            payload = resp.json()
            items = payload.get("items", [])

            results: list[SearchResult] = []
            for item in items[:max_results]:
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("link", ""),
                    snippet=item.get("snippet", ""),
                ))
            return results

        except httpx.HTTPStatusError as exc:
            logger.error(
                "google_cse_search_http_error: status=%s query=%s",
                exc.response.status_code,
                query[:80],
            )
            return []
        except Exception as exc:
            logger.error("google_cse_search_failed: %s", exc)
            return []

    async def scrape(self, url: str) -> ScrapedPage | None:
        """Google CSE is search-only; scraping delegates to HttpxFallbackProvider."""
        return None


# ── Httpx Fallback Provider ───────────────────────────────────

class HttpxFallbackProvider:
    """Last-resort provider: raw HTTP GET + html2text conversion.

    - search() always returns empty (no search API)
    - scrape() fetches the URL and converts HTML to markdown via html2text

    Graceful if html2text is not installed: falls back to raw HTML truncated.
    """

    name: str = "httpx_fallback"

    _MAX_BODY_BYTES = 2 * 1024 * 1024  # 2 MB cap

    def __init__(self) -> None:
        self._h2t: Any = None
        self._h2t_checked = False

    def _get_html2text(self) -> Any:
        """Lazy-load html2text; returns converter instance or None."""
        if self._h2t_checked:
            return self._h2t

        self._h2t_checked = True
        try:
            import html2text as _h2t
            converter = _h2t.HTML2Text()
            converter.ignore_links = False
            converter.ignore_images = True
            converter.body_width = 0  # no wrapping
            self._h2t = converter
            return converter
        except ImportError:
            logger.info(
                "httpx_fallback: html2text not installed — raw HTML will be truncated"
            )
            return None

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """No search capability — always returns empty."""
        return []

    async def scrape(self, url: str) -> ScrapedPage | None:
        try:
            async with httpx.AsyncClient(
                timeout=20.0,
                follow_redirects=True,
                headers={"User-Agent": "NexSidi/2.0 (web-research-agent)"},
            ) as client:
                resp = await client.get(url)
                resp.raise_for_status()

            content_type = resp.headers.get("content-type", "")
            if "text/html" not in content_type and "text/plain" not in content_type:
                logger.info(
                    "httpx_fallback_skip: non-text content_type=%s url=%s",
                    content_type[:40],
                    url[:80],
                )
                return None

            html = resp.text[: self._MAX_BODY_BYTES]

            converter = self._get_html2text()
            if converter is not None:
                markdown = converter.handle(html)
            else:
                # Crude fallback: strip tags manually
                markdown = html[:50_000]

            # Extract <title> if present
            title = ""
            lower_html = html[:4096].lower()
            start = lower_html.find("<title")
            if start != -1:
                tag_end = lower_html.find(">", start)
                close = lower_html.find("</title>", tag_end)
                if tag_end != -1 and close != -1:
                    title = html[tag_end + 1 : close].strip()

            return ScrapedPage(
                url=str(resp.url),
                title=title,
                markdown=markdown,
                metadata={"provider": "httpx_fallback"},
            )

        except httpx.HTTPStatusError as exc:
            logger.error(
                "httpx_fallback_scrape_http_error: status=%s url=%s",
                exc.response.status_code,
                url[:80],
            )
            return None
        except Exception as exc:
            logger.error("httpx_fallback_scrape_failed: url=%s error=%s", url[:80], exc)
            return None


# ── Multi-Provider Orchestrator ───────────────────────────────

class MultiProviderSearch:
    """Tries a chain of providers in priority order.

    - search(): returns the first non-empty result list
    - scrape(): returns the first successful scrape
    - Logs which provider ultimately served the request
    """

    def __init__(self, providers: list[WebProvider]) -> None:
        self._providers = providers
        provider_names = [p.name for p in providers]
        logger.info("multi_provider_search: chain=%s", provider_names)

    @classmethod
    def from_env(cls) -> MultiProviderSearch:
        """Auto-detect available providers from environment variables.

        Priority order:
        1. Firecrawl  (if FIRECRAWL_API_KEY is set)
        2. Google CSE  (if GOOGLE_CSE_API_KEY + GOOGLE_CSE_ID are set)
        3. Httpx fallback (always available, scrape-only)
        """
        providers: list[WebProvider] = []

        if os.environ.get("FIRECRAWL_API_KEY"):
            providers.append(FirecrawlProvider())

        if os.environ.get("GOOGLE_CSE_API_KEY") and os.environ.get("GOOGLE_CSE_ID"):
            providers.append(GoogleSearchProvider())

        # Always include the httpx fallback for scraping
        providers.append(HttpxFallbackProvider())

        return cls(providers)

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Try each provider until one returns non-empty results."""
        for provider in self._providers:
            try:
                results = await provider.search(query, max_results)
                if results:
                    logger.info(
                        "multi_provider_search_hit: provider=%s query=%s results=%d",
                        provider.name,
                        query[:80],
                        len(results),
                    )
                    return results
            except Exception as exc:
                logger.warning(
                    "multi_provider_search_skip: provider=%s error=%s",
                    provider.name,
                    exc,
                )
                continue

        logger.warning("multi_provider_search_exhausted: query=%s", query[:80])
        return []

    async def scrape(self, url: str) -> ScrapedPage | None:
        """Try each provider until one successfully scrapes the URL."""
        for provider in self._providers:
            try:
                page = await provider.scrape(url)
                if page is not None:
                    logger.info(
                        "multi_provider_scrape_hit: provider=%s url=%s chars=%d",
                        provider.name,
                        url[:80],
                        len(page.markdown),
                    )
                    return page
            except Exception as exc:
                logger.warning(
                    "multi_provider_scrape_skip: provider=%s error=%s",
                    provider.name,
                    exc,
                )
                continue

        logger.warning("multi_provider_scrape_exhausted: url=%s", url[:80])
        return None


# ── Singleton ─────────────────────────────────────────────────

_multi_provider: MultiProviderSearch | None = None


def get_multi_provider_search() -> MultiProviderSearch:
    """Get or create the multi-provider search singleton.

    Providers are auto-detected from environment variables.
    """
    global _multi_provider
    if _multi_provider is None:
        _multi_provider = MultiProviderSearch.from_env()
    return _multi_provider
