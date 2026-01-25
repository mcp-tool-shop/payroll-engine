"""FastAPI dependencies for dependency injection."""

from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy.ext.asyncio import AsyncSession

from payroll_engine.database import async_session_factory


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """Get database session dependency."""
    async with async_session_factory() as session:
        try:
            yield session
        finally:
            await session.close()


async def get_tenant_id(
    x_tenant_id: Annotated[str | None, Header()] = None
) -> UUID:
    """Extract tenant ID from header."""
    if not x_tenant_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-ID header is required",
        )
    try:
        return UUID(x_tenant_id)
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid X-Tenant-ID format",
        )


# Type aliases for cleaner dependency injection
DbSession = Annotated[AsyncSession, Depends(get_db_session)]
TenantId = Annotated[UUID, Depends(get_tenant_id)]
