"""Phase 1A API contracts owned by backend_core."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role


class LoginInput(BaseModel):
    department_id: UUID
    password: SecretStr = Field(min_length=1, max_length=256)
    remember_me: bool = False


class SelectOperatorInput(BaseModel):
    operator_id: UUID


class ResetPasswordInput(BaseModel):
    new_password: SecretStr = Field(min_length=12, max_length=128)


class DepartmentPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    name: str
    status: DepartmentStatus


class OperatorPublic(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    department_id: UUID
    name: str
    role: Role
    status: OperatorStatus


class LoginPublic(BaseModel):
    department: DepartmentPublic
    role: Role
    operator_required: bool
    expires_at: datetime


class AuthMePublic(BaseModel):
    department: DepartmentPublic
    operator: OperatorPublic | None
    role: Role
    expires_at: datetime


class PasswordResetPublic(BaseModel):
    department_id: UUID
    revoked_sessions: int
