"""Application configuration loaded from environment variables."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env", env_file_encoding="utf-8", extra="ignore"
    )

    # LLM (Azure OpenAI)
    azure_openai_api_key: str = ""
    azure_openai_endpoint: str = ""
    azure_openai_api_version: str = "2024-10-21"
    # Responses API (web_search). Chat uses azure_openai_api_version above;
    # web search needs the v1 Responses route or a preview api-version.
    azure_openai_responses_api_version: str = "2025-03-01-preview"
    llm_model: str = "gpt-4o-mini"  # Azure deployment name
    # Optional deployment for Responses web_search; defaults to llm_model.
    web_search_model: str = ""
    web_search_timeout_s: float = 120.0
    llm_temperature: float = 0.1
    llm_max_retries: int = 2
    llm_timeout_s: float = 45.0
    # Optional Azure deployment name for the email writer. Leave empty to use
    # llm_model; set to your highest-quality deployment for better copy.
    writer_llm_model: str = "gpt-4o"
    writer_llm_temperature: float = 0.55

    # Embeddings / NLI (HuggingFace, CPU)
    embedding_model: str = "sentence-transformers/all-MiniLM-L6-v2"
    nli_model: str = "cross-encoder/nli-deberta-v3-xsmall"
    nli_entailment_threshold: float = 0.55

    # Crawling
    crawl_max_pages: int = 12
    crawl_max_depth: int = 2
    crawl_page_timeout_s: float = 20.0
    crawl_concurrency: int = 6
    crawl_headless: bool = True
    crawl_user_agent: str = (
        "ArtisanEvidenceBot/1.0 (+https://artisan.example/evidence-bot)"
    )
    # When the static fetch yields a body shorter than this many chars after
    # trafilatura cleanup, we re-fetch with Playwright (JS rendered). Set to
    # 0 to disable Playwright fallback entirely.
    crawl_js_fallback_min_chars: int = 400

    # Sectioning
    max_section_chars: int = 1800
    section_overlap_chars: int = 120

    # LLM extraction parallelism. Batches are independent so we fan them out
    # concurrently. Tune this with Azure rate limits in mind (RPM/TPM).
    extract_concurrency: int = 10
    extract_batch_size: int = 10

    # Target crawl + extraction limits (one pass; no fetch_more in target flow).
    target_crawl_max_pages: int = 5
    target_crawl_max_depth: int = 1
    target_max_crawl_passes: int = 1
    target_max_sections_for_extraction: int = 40

    # Sender fetch_more repair pass (at most once; capped page count).
    fetch_more_max_pages: int = 2

    # External signal provider (target enrichment only)
    external_signal_provider: Literal["disabled", "openai_web_search"] = "disabled"
    external_signal_max_results: int = 6

    # Storage
    data_dir: Path = Path("data")
    db_path: Path = Path("data/artisan.db")
    crawl_cache_dir: Path = Path("data/crawl")

    # Observability
    mlflow_tracking_uri: str = "file:./data/mlruns"
    mlflow_experiment: str = "artisan-outbound"

    # CORS
    cors_origins: str = "http://localhost:3000"

    def ensure_dirs(self) -> None:
        self.data_dir.mkdir(parents=True, exist_ok=True)
        self.crawl_cache_dir.mkdir(parents=True, exist_ok=True)


settings = Settings()
settings.ensure_dirs()
