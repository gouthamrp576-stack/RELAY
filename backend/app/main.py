"""Entry point for the RELAY backend application."""

from fastapi import FastAPI

from backend.app.api.donations import router as donations_router
from backend.app.api.health import router as health_router


def create_app() -> FastAPI:
    """Create the application and register its API routes."""

    application = FastAPI(
        title="RELAY API",
        description=(
            "Human-supervised surplus-food rescue coordination."
        ),
        version="0.1.0",
    )

    application.include_router(
        health_router,
        prefix="/api/v1",
    )

    application.include_router(
        donations_router,
        prefix="/api/v1",
    )

    return application


app = create_app()