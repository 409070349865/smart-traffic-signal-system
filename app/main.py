"""FastAPI entry point (Phase 1: health endpoint only; routers added later)."""
from __future__ import annotations

import logging

from fastapi import FastAPI

from app.config import get_settings, setup_logging

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    settings = get_settings()
    setup_logging(settings.app.log_level)

    application = FastAPI(
        title="Smart Traffic Signal System",
        description=settings.safety_notice,
        version="0.1.0",
    )

    @application.get("/api/health", tags=["system"])
    async def health() -> dict[str, str]:
        return {
            "status": "ok",
            "version": application.version,
            "notice": settings.safety_notice,
        }

    logger.info("Application created.")
    return application


app = create_app()
