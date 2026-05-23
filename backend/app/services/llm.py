"""LLM wrapper built on Instructor + Pydantic structured outputs.

Every LLM call goes through here so token / cost accounting and timeouts
are applied uniformly. Instructor handles JSON parsing, schema validation,
and retry-on-validation-failure under the hood, so we no longer hand-roll
JSON repair prompts.
"""
from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field
from typing import TypeVar

import instructor
from openai import AzureOpenAI, OpenAI
from pydantic import BaseModel

from ..config import settings

log = logging.getLogger(__name__)

T = TypeVar("T", bound=BaseModel)


# Approximate USD per 1K tokens for common models. Used for budget visibility.
_PRICING: dict[str, tuple[float, float]] = {
    "gpt-4o-mini": (0.00015, 0.0006),
    "gpt-4o": (0.0025, 0.01),
    "gpt-4.1-mini": (0.0004, 0.0016),
}


@dataclass
class UsageAccumulator:
    tokens_in: int = 0
    tokens_out: int = 0
    cost_usd: float = 0.0
    calls: int = 0
    by_purpose: dict[str, dict[str, float]] = field(default_factory=dict)
    # Concurrent LLM calls (extract batches run in parallel via to_thread)
    # mutate this accumulator; guard the writes.
    _lock: threading.Lock = field(
        default_factory=threading.Lock, repr=False, compare=False
    )

    def add(self, model: str, tin: int, tout: int, purpose: str) -> None:
        p_in, p_out = _PRICING.get(model, (0.0, 0.0))
        delta = (tin / 1000.0) * p_in + (tout / 1000.0) * p_out
        with self._lock:
            self.tokens_in += tin
            self.tokens_out += tout
            self.calls += 1
            self.cost_usd += delta
            bucket = self.by_purpose.setdefault(
                purpose, {"calls": 0, "tokens_in": 0, "tokens_out": 0, "cost_usd": 0.0}
            )
            bucket["calls"] += 1
            bucket["tokens_in"] += tin
            bucket["tokens_out"] += tout
            bucket["cost_usd"] += delta


class LLMClient:
    def __init__(self) -> None:
        missing = []
        if not settings.azure_openai_api_key:
            missing.append("AZURE_OPENAI_API_KEY")
        if not settings.azure_openai_endpoint:
            missing.append("AZURE_OPENAI_ENDPOINT")
        if missing:
            raise RuntimeError(
                f"{', '.join(missing)} required. Set them in the environment."
            )
        self._raw = AzureOpenAI(
            api_key=settings.azure_openai_api_key,
            api_version=settings.azure_openai_api_version,
            azure_endpoint=settings.azure_openai_endpoint.rstrip("/"),
            timeout=settings.llm_timeout_s,
        )
        # Instructor's tool-mode (structured outputs) is the strictest path:
        # the model is forced to call a function with the schema, so we get
        # parsed Pydantic objects directly.
        self._client = instructor.from_openai(
            self._raw, mode=instructor.Mode.TOOLS_STRICT
        )

    def structured(
        self,
        *,
        system: str,
        user: str,
        schema: type[T],
        purpose: str,
        usage: UsageAccumulator,
        model: str | None = None,
        temperature: float | None = None,
        max_retries: int | None = None,
    ) -> T:
        """Call the LLM and return a validated ``schema`` instance.

        Instructor automatically retries on validation failure (up to
        ``max_retries``), so we don't have to hand-roll repair prompts.
        """
        t0 = time.time()
        mdl = model or settings.llm_model
        temp = settings.llm_temperature if temperature is None else temperature
        retries = settings.llm_max_retries if max_retries is None else max_retries

        result, completion = self._client.chat.completions.create_with_completion(
            model=mdl,
            temperature=temp,
            response_model=schema,
            max_retries=retries,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
        )
        usage_obj = getattr(completion, "usage", None)
        tin = int(getattr(usage_obj, "prompt_tokens", 0) or 0)
        tout = int(getattr(usage_obj, "completion_tokens", 0) or 0)
        usage.add(mdl, tin, tout, purpose)
        log.debug(
            "llm purpose=%s model=%s tin=%d tout=%d dur=%.2fs",
            purpose,
            mdl,
            tin,
            tout,
            time.time() - t0,
        )
        return result

    @property
    def raw_openai(self) -> OpenAI | AzureOpenAI:
        """Underlying OpenAI SDK client for capabilities Instructor doesn't wrap (e.g. web_search)."""
        return self._raw


_client_singleton: LLMClient | None = None


def get_llm() -> LLMClient:
    global _client_singleton
    if _client_singleton is None:
        _client_singleton = LLMClient()
    return _client_singleton
