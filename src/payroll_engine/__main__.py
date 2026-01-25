"""Entry point for running the application with uvicorn."""

import uvicorn

from payroll_engine.config import settings


def main() -> None:
    """Run the application."""
    uvicorn.run(
        "payroll_engine.api.app:app",
        host=settings.HOST,
        port=settings.PORT,
        reload=settings.DEBUG,
    )


if __name__ == "__main__":
    main()
