"""API routes."""

from payroll_engine.api.routes.pay_runs import router as pay_runs_router
from payroll_engine.api.routes.health import router as health_router

__all__ = ["pay_runs_router", "health_router"]
