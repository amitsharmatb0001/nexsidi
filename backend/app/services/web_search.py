"""Firecrawl web search service for agent research capabilities.

Provides:
- search(query) — web search via Firecrawl Search API
- scrape(url)  — page scrape with markdown extraction
- crawl(url)   — multi-page site crawl

API key loaded from GCP Secret Manager (FIRECRAWL_API_KEY).
Falls back gracefully: if no API key, returns empty results with warning.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger("nexsidi.web_search")


@dataclass(frozen=True, slots=True)
class SearchResult:
    """A single web search result."""

    title: str
    url: str
    snippet: str
    content: str = ""  # Full page content (if available)


@dataclass(frozen=True, slots=True)
class ScrapedPage:
    """A scraped web page with markdown content."""

    url: str
    title: str
    markdown: str
    metadata: dict[str, Any] = field(default_factory=dict)


class WebSearchService:
    """Web search and scraping via Firecrawl API.

    Used by agents for:
    - Researching documentation and best practices
    - Finding error solutions and StackOverflow answers
    - Analyzing competitor features and market trends
    - Looking up framework-specific APIs and patterns

    Graceful degradation: if FIRECRAWL_API_KEY is not set,
    all methods return empty results with a warning log.
    """

    def __init__(self) -> None:
        self._api_key: str | None = None
        self._client: Any = None
        self._initialized = False

    def _ensure_init(self) -> bool:
        """Lazy-init Firecrawl client. Returns True if ready."""
        if self._initialized:
            return self._client is not None

        self._initialized = True
        self._api_key = os.environ.get("FIRECRAWL_API_KEY", "")

        if not self._api_key:
            logger.warning(
                "web_search_disabled: FIRECRAWL_API_KEY not set. "
                "Add it to GCP Secret Manager to enable agent web search."
            )
            return False

        try:
            from firecrawl import FirecrawlApp
            self._client = FirecrawlApp(api_key=self._api_key)
            logger.info("web_search_enabled: Firecrawl client initialized")
            return True
        except ImportError:
            logger.warning(
                "web_search_disabled: firecrawl-py not installed. "
                "Install with: pip install firecrawl-py"
            )
            return False
        except Exception as exc:
            logger.error("web_search_init_failed: %s", exc)
            return False

    async def search(self, query: str, max_results: int = 5) -> list[SearchResult]:
        """Search the web for documentation, best practices, error solutions.

        Args:
            query: Search query string.
            max_results: Maximum number of results to return (default 5).

        Returns:
            List of SearchResult with title, URL, snippet, and optional content.
        """
        if not self._ensure_init():
            return []

        try:
            # Firecrawl search API
            response = self._client.search(
                query=query,
                params={"limit": max_results},
            )

            results: list[SearchResult] = []
            # Handle both v1 and v2 response formats
            data = response if isinstance(response, list) else response.get("data", [])
            for item in data[:max_results]:
                results.append(SearchResult(
                    title=item.get("title", ""),
                    url=item.get("url", ""),
                    snippet=item.get("description", item.get("snippet", "")),
                    content=item.get("markdown", item.get("content", "")),
                ))

            logger.info(
                "web_search_complete",
                query=query[:80],
                results=len(results),
            )
            return results

        except Exception as exc:
            logger.error("web_search_failed: query=%s error=%s", query[:80], exc)
            return []

    async def scrape(self, url: str) -> ScrapedPage | None:
        """Scrape a specific URL and extract markdown content.

        Args:
            url: The URL to scrape.

        Returns:
            ScrapedPage with markdown content, or None if failed.
        """
        if not self._ensure_init():
            return None

        try:
            response = self._client.scrape_url(
                url=url,
                params={"formats": ["markdown"]},
            )

            # Handle both v1 and v2 response formats
            data = response if isinstance(response, dict) else {}
            markdown = data.get("markdown", data.get("content", ""))
            metadata = data.get("metadata", {})

            page = ScrapedPage(
                url=url,
                title=metadata.get("title", ""),
                markdown=markdown,
                metadata=metadata,
            )

            logger.info(
                "web_scrape_complete",
                url=url[:80],
                chars=len(markdown),
            )
            return page

        except Exception as exc:
            logger.error("web_scrape_failed: url=%s error=%s", url[:80], exc)
            return None

    async def crawl(self, url: str, max_pages: int = 10) -> list[ScrapedPage]:
        """Crawl a website starting from the given URL.

        Args:
            url: Starting URL for the crawl.
            max_pages: Maximum number of pages to crawl (default 10).

        Returns:
            List of ScrapedPage objects.
        """
        if not self._ensure_init():
            return []

        try:
            response = self._client.crawl_url(
                url=url,
                params={"limit": max_pages},
                poll_interval=5,
            )

            pages: list[ScrapedPage] = []
            data = response if isinstance(response, list) else response.get("data", [])
            for item in data[:max_pages]:
                metadata = item.get("metadata", {})
                pages.append(ScrapedPage(
                    url=item.get("url", url),
                    title=metadata.get("title", ""),
                    markdown=item.get("markdown", ""),
                    metadata=metadata,
                ))

            logger.info(
                "web_crawl_complete",
                url=url[:80],
                pages=len(pages),
            )
            return pages

        except Exception as exc:
            logger.error("web_crawl_failed: url=%s error=%s", url[:80], exc)
            return []


# ── Singleton ──────────────────────────────────────────────────

_web_search: WebSearchService | None = None


def get_web_search() -> WebSearchService:
    """Get or create the web search service singleton."""
    global _web_search
    if _web_search is None:
        _web_search = WebSearchService()
    return _web_search


# ── Multi-Provider Facade ──────────────────────────────────────

_multi_search: Any = None


def get_multi_provider_search() -> Any:
    """Get the multi-provider web search (auto-detects available providers).

    Falls back to the basic Firecrawl-only WebSearchService if the
    multi-provider module is not available.
    """
    global _multi_search
    if _multi_search is None:
        try:
            from app.services.web_providers import get_multi_provider_search as _get_multi
            _multi_search = _get_multi()
        except ImportError:
            logger.info("multi_provider_search_unavailable: using single-provider Firecrawl")
            _multi_search = get_web_search()
    return _multi_search
