"""Azure OpenAI Responses API — ``web_search`` tool.

Target discovery and target-flow enrichment need live web results. That
uses the **Responses API** with ``tools=[{"type": "web_search"}]``, not
the Chat Completions API.

The rest of Artisan (sender ICP, extraction, emails) uses
``AzureOpenAI`` + ``chat.completions`` with ``AZURE_OPENAI_API_VERSION``
(typically ``2024-10-21``). That client **cannot** run ``web_search``;
calls fail and the UI showed "Discovery unavailable".

Microsoft Foundry expects Responses + web search via the **v1 base URL**:

  ``https://{resource}.openai.azure.com/openai/v1/``

using the standard ``OpenAI`` SDK client (same API key), **not** the
legacy ``AzureOpenAI`` + old ``api-version`` query pattern.

See: https://learn.microsoft.com/en-us/azure/foundry/openai/how-to/web-search

This module centralizes the correct client(s), parsing, and errors.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Literal

import openai
from openai import AzureOpenAI, OpenAI

from ..config import settings

log = logging.getLogger(__name__)

# client.responses was added in openai-python 1.66.0 (we pin 1.74.1+).
_MIN_OPENAI_FOR_RESPONSES = (1, 66, 0)


def _parse_version_tuple(version: str) -> tuple[int, ...]:
    parts: list[int] = []
    for piece in version.split(".")[:3]:
        digits = "".join(ch for ch in piece if ch.isdigit())
        if digits:
            parts.append(int(digits))
    return tuple(parts)


def _responses_supported() -> bool:
    try:
        return _parse_version_tuple(openai.__version__) >= _MIN_OPENAI_FOR_RESPONSES
    except Exception:  # noqa: BLE001
        return hasattr(OpenAI(api_key="x"), "responses")


def _require_responses_client(client: OpenAI | AzureOpenAI, label: str) -> None:
    if not hasattr(client, "responses"):
        raise RuntimeError(
            f"{label} has no .responses (installed openai=={openai.__version__}; "
            f"need openai>={'.'.join(map(str, _MIN_OPENAI_FOR_RESPONSES))}). "
            "Rebuild the backend image: docker compose build --no-cache backend"
        )


@dataclass
class WebSearchCitation:
    url: str
    title: str = ""
    snippet: str = ""


@dataclass
class WebSearchOutcome:
    ok: bool
    raw_text: str = ""
    citations: list[WebSearchCitation] = field(default_factory=list)
    error: str = ""
    client_used: str = ""


def responses_v1_base_url() -> str:
    """Normalize ``AZURE_OPENAI_ENDPOINT`` to the Responses v1 base URL."""
    endpoint = (settings.azure_openai_endpoint or "").strip().rstrip("/")
    if not endpoint:
        return ""
    if "/openai/v1" in endpoint:
        return endpoint if endpoint.endswith("/") else f"{endpoint}/"
    return f"{endpoint}/openai/v1/"


def _parse_responses_output(resp: Any) -> tuple[str, list[WebSearchCitation]]:
    text_chunks: list[str] = []
    citations: dict[str, WebSearchCitation] = {}
    for item in getattr(resp, "output", []) or []:
        if getattr(item, "type", None) != "message":
            continue
        for content in getattr(item, "content", []) or []:
            text = getattr(content, "text", "") or ""
            if text:
                text_chunks.append(text)
            for ann in getattr(content, "annotations", []) or []:
                if getattr(ann, "type", "") != "url_citation":
                    continue
                url = getattr(ann, "url", "") or ""
                if not url or url in citations:
                    continue
                start = getattr(ann, "start_index", None) or 0
                end = getattr(ann, "end_index", None) or min(len(text), start + 320)
                snippet = text[max(0, start - 80) : end + 80].strip()
                citations[url] = WebSearchCitation(
                    url=url,
                    title=(getattr(ann, "title", "") or "")[:200],
                    snippet=snippet[:600],
                )
    return "\n\n".join(text_chunks).strip(), list(citations.values())


def _create_v1_client() -> OpenAI:
    base_url = responses_v1_base_url()
    if not base_url:
        raise RuntimeError("AZURE_OPENAI_ENDPOINT is not set")
    return OpenAI(
        api_key=settings.azure_openai_api_key,
        base_url=base_url,
        timeout=settings.web_search_timeout_s,
    )


def _create_preview_azure_client() -> AzureOpenAI:
    """Fallback: AzureOpenAI with a Responses-capable preview API version."""
    return AzureOpenAI(
        api_key=settings.azure_openai_api_key,
        api_version=settings.azure_openai_responses_api_version,
        azure_endpoint=settings.azure_openai_endpoint.rstrip("/"),
        timeout=settings.web_search_timeout_s,
    )


def run_web_search(
    *,
    input: str | list[dict[str, str]],
    model: str | None = None,
    tool_choice: Literal["auto", "required"] | None = "auto",
    instructions: str | None = None,
) -> WebSearchOutcome:
    """Call Azure-hosted ``web_search`` via the Responses API.

    Tries the Foundry v1 ``OpenAI`` client first, then falls back to
    ``AzureOpenAI`` with a preview ``api_version`` if configured.
    """
    mdl = model or settings.web_search_model or settings.llm_model
    tools: list[dict[str, str]] = [{"type": "web_search"}]
    kwargs: dict[str, Any] = {
        "model": mdl,
        "tools": tools,
        "input": input,
    }
    if instructions:
        kwargs["instructions"] = instructions
    if tool_choice:
        kwargs["tool_choice"] = tool_choice

    if not _responses_supported():
        return WebSearchOutcome(
            ok=False,
            error=(
                f"openai package {openai.__version__} is too old for Responses/web_search "
                f"(need >={'.'.join(map(str, _MIN_OPENAI_FOR_RESPONSES))}). "
                "Rebuild backend: docker compose build --no-cache backend"
            ),
        )

    errors: list[str] = []

    # 1) Recommended path: OpenAI client + /openai/v1/ base URL (Foundry GA).
    try:
        client = _create_v1_client()
        _require_responses_client(client, "OpenAI v1 client")
        resp = client.responses.create(**kwargs)
        raw, cites = _parse_responses_output(resp)
        log.info(
            "web_search ok (v1) model=%s text_chars=%d citations=%d",
            mdl,
            len(raw),
            len(cites),
        )
        return WebSearchOutcome(
            ok=True,
            raw_text=raw,
            citations=cites,
            client_used="azure_responses_v1",
        )
    except Exception as e:  # noqa: BLE001
        msg = f"v1 Responses ({responses_v1_base_url()}): {e}"
        errors.append(msg)
        log.warning("web_search v1 failed: %s", e)

    # 2) Fallback: AzureOpenAI + preview api-version (older resources).
    try:
        client = _create_preview_azure_client()
        _require_responses_client(client, "AzureOpenAI preview client")
        resp = client.responses.create(**kwargs)
        raw, cites = _parse_responses_output(resp)
        log.info(
            "web_search ok (preview) model=%s text_chars=%d citations=%d",
            mdl,
            len(raw),
            len(cites),
        )
        return WebSearchOutcome(
            ok=True,
            raw_text=raw,
            citations=cites,
            client_used="azure_openai_preview",
        )
    except Exception as e:  # noqa: BLE001
        msg = f"preview Responses (api-version={settings.azure_openai_responses_api_version}): {e}"
        errors.append(msg)
        log.warning("web_search preview failed: %s", e)

    detail = " | ".join(errors) if errors else "unknown error"
    log.error("web_search unavailable after all attempts: %s", detail)
    return WebSearchOutcome(ok=False, error=detail)
