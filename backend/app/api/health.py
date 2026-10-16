"""Endpoints for checking whether the RELAY API is responding."""

from datetime import datetime, timezone

from fastapi import APIRouter


router = APIRouter(tags=["System"])


@router.get("/health")
def health_check() -> dict[str, str]:
    """Report that the API process is running."""
    return {
        "status": "ok",
        "service": "relay-backend",
        "timestamp": datetime.now(timezone.utc).isoformat(),
    }