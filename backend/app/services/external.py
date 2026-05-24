"""External signal provider.

The original implementation pulled in Tavily + DuckDuckGo. We replace that
with a single typed interface and two implementations:

- ``DisabledExternalSignalProvider`` -- the default. Returns no results.
  External enrichment is OFF by default and the sender flow never uses it.
- ``OpenAIWebSearchProvider`` -- uses the OpenAI Responses API ``web_search``
  tool. Results are converted to the same ``Section`` format as website
  evidence so downstream code is agnostic to the source.

The choice is controlled by ``EXTERNAL_SIGNAL_PROVIDER`` env var.

Protected platforms (LinkedIn, Maps, Facebook, X) are always filtered out
regardless of provider.
"""
from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Protocol
from urllib.parse import urlparse

from ..config import settings
from .llm import LLMClient
from .web_search import run_web_search

log = logging.getLogger(__name__)


_BLOCKED_DOMAINS = {
    "linkedin.com",
    "google.com/maps",
    "facebook.com",
    "instagram.com",
    "twitter.com",
    "x.com",
}


@dataclass
class SearchHit:
    url: str
    title: str
    snippet: str


def _allowed(url: str) -> bool:
    if not url:
        return False
    host = urlparse(url).netloc.lower()
    if host.startswith("www."):
        host = host[4:]
    return not any(host.endswith(b) for b in _BLOCKED_DOMAINS)


class ExternalSignalProvider(Protocol):
    name: str

    def search(self, query: str, k: int) -> list[SearchHit]:
        ...


class DisabledExternalSignalProvider:
    """Default provider. Always returns nothing."""

    name = "disabled"

    def search(self, query: str, k: int) -> list[SearchHit]:  # noqa: ARG002
        log.info("external: disabled (skipping query='%s')", query[:60])
        return []


class OpenAIWebSearchProvider:
    """Uses OpenAI Responses API web_search tool.

    OpenAI returns answers with annotated citations (URLs + titles). We
    parse the annotations into ``SearchHit`` objects with the most recent
    answer text as a snippet per URL.
    """

    name = "openai_web_search"

    def __init__(self, llm: LLMClient) -> None:
        self._llm = llm

    def search(self, query: str, k: int) -> list[SearchHit]:
        outcome = run_web_search(
            input=(
                f"Find recent public signals about: {query}. "
                f"Focus on news, funding, hiring, expansion, leadership changes, "
                f"or product launches. Cite the source URLs."
            ),
            tool_choice="auto",
        )
        if not outcome.ok:
            log.warning("openai web_search failed: %s", outcome.error)
            return []

        hits: dict[str, SearchHit] = {}
        for c in outcome.citations:
            if not _allowed(c.url) or c.url in hits:
                continue
            hits[c.url] = SearchHit(url=c.url, title=c.title, snippet=c.snippet)
            if len(hits) >= k:
                break
        out = list(hits.values())[:k]
        log.info("openai_web_search '%s' -> %d hits", query[:60], len(out))
        return out


def get_external_provider(llm: LLMClient) -> ExternalSignalProvider:
    name = settings.external_signal_provider
    if name == "openai_web_search":
        return OpenAIWebSearchProvider(llm)
    return DisabledExternalSignalProvider()


def hits_to_sections(hits: list[SearchHit], company_id: str) -> list[dict]:  # noqa: ARG001
    """Convert external snippets into the same section dict format website pages use."""
    out: list[dict] = []
    for h in hits:
        if not h.snippet:
            continue
        sid = (
            "sec_ws_"
            + hashlib.sha1(f"{h.url}|{h.title}".encode("utf-8")).hexdigest()[:10]
        )
        text = f"{h.title}\n\n{h.snippet}".strip()
        out.append(
            {
                "section_id": sid,
                "url": h.url,
                "heading": h.title or None,
                "text": text,
                "char_start": 0,
                "char_end": len(text),
                "source": "web_search",
            }
        )
    return out
