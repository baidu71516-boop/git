"""Public active Department lookup for the login form."""

from typing import Annotated, Any

from backend_core.auth.repository import AuthRepository
from backend_core.auth.schemas import DepartmentPublic
from fastapi import APIRouter, Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.http.dependencies import get_database_session
from app.http.responses import envelope

router = APIRouter(prefix="/api/v1/departments", tags=["departments"])


@router.get("")
async def list_departments(
    request: Request,
    session: Annotated[AsyncSession, Depends(get_database_session)],
) -> dict[str, Any]:
    departments = await AuthRepository(session).list_active_departments()
    public = [DepartmentPublic.model_validate(department) for department in departments]
    return envelope(request, data=public)
