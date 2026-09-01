"""Phase 1A API contracts owned by backend_core."""

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, SecretStr, StrictStr

from backend_core.auth.enums import DepartmentStatus, ModuleKey, OperatorStatus, Role


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


class OperatorAdminCreateInput(BaseModel):
    """Closed request contract for one new Department-local Operator."""

    model_config = ConfigDict(extra="forbid")

    name: StrictStr = Field(min_length=1, max_length=120)
    role: StrictStr = Field(min_length=1, max_length=32)
    module_grants: list[StrictStr]


class OperatorAdminUpdateInput(BaseModel):
    """One atomic, optimistic-concurrency Operator administration mutation."""

    model_config = ConfigDict(extra="forbid")

    expected_updated_at: datetime
    name: StrictStr | None = Field(default=None, min_length=1, max_length=120)
    role: StrictStr | None = Field(default=None, min_length=1, max_length=32)
    status: StrictStr | None = Field(default=None, min_length=1, max_length=32)
    module_grants: list[StrictStr] | None = None


class OperatorAdminPublic(BaseModel):
    """Safe future-Web representation; grants are persisted positive grants only."""

    id: UUID
    name: str
    role: Role
    status: OperatorStatus
    module_grants: tuple[ModuleKey, ...]
    created_at: datetime
    updated_at: datetime
