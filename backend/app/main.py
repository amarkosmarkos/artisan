"""FastAPI application entry point."""
from __future__ import annotations

import asyncio
import logging
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .db import get_conn
from .routes import dashboard, flows, metrics, sender_targets


logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-7s %(name)s :: %(message)s",
)


def create_app() -> FastAPI:
    app = FastAPI(
        title="Artisan Evidence-First Outbound",
        description=(
            "Auditable outbound strategy system. Every commercial claim is "
            "grounded in public evidence and tagged with its verification status."
        ),
        version="0.1.0",
    )

    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    app.include_router(flows.router, prefix="/api/v1", tags=["flows"])
    app.include_router(dashboard.router, prefix="/api/v1", tags=["dashboard"])
    app.include_router(metrics.router, prefix="/api/v1", tags=["metrics"])
    app.include_router(
        sender_targets.router, prefix="/api/v1", tags=["sender-targets"]
    )

    @app.on_event("startup")
    def _startup() -> None:
        # Eagerly initialize the DB so a first request doesn't pay schema cost.
        get_conn()
        # Generous default executor so `asyncio.to_thread` LLM/NLI workers
        # cannot starve the FastAPI request-handling thread pool. Python's
        # default is min(32, cpu+4) which on small containers is too tight
        # once we run extract_concurrency + NLI + ad-hoc sync code.
        loop = asyncio.get_event_loop()
        loop.set_default_executor(
            ThreadPoolExecutor(max_workers=64, thread_name_prefix="artisan-worker")
        )

    @app.get("/api/v1/health")
    async def health() -> dict:
        # Must be async: sync routes run in the anyio threadpool, which we
        # already saturate with asyncio.to_thread LLM/NLI workers during a
        # pipeline run. A sync health endpoint would hang under load.
        return {
            "ok": True,
            "llm_model": settings.llm_model,
            "writer_llm_model": settings.writer_llm_model or settings.llm_model,
            "embedding_model": settings.embedding_model,
            "nli_model": settings.nli_model,
        }

    return app


app = create_app()
