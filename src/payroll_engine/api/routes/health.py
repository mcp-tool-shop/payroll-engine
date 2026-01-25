"""Health check endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, status
from pydantic import BaseModel
from sqlalchemy import text

from payroll_engine.api.dependencies import DbSession

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    """Health check response."""

    status: str
    timestamp: datetime
    database: str


@router.get(
    "/health",
    response_model=HealthResponse,
    status_code=status.HTTP_200_OK,
)
async def health_check(db: DbSession) -> HealthResponse:
    """Check API and database health."""
    db_status = "unhealthy"
    try:
        await db.execute(text("SELECT 1"))
        db_status = "healthy"
    except Exception:
        pass

    return HealthResponse(
        status="healthy" if db_status == "healthy" else "degraded",
        timestamp=datetime.now(timezone.utc),
        database=db_status,
    )


@router.get("/ready", status_code=status.HTTP_200_OK)
async def readiness_check() -> dict[str, str]:
    """Readiness check for container orchestration."""
    return {"status": "ready"}


@router.get("/live", status_code=status.HTTP_200_OK)
async def liveness_check() -> dict[str, str]:
    """Liveness check for container orchestration."""
    return {"status": "alive"}
