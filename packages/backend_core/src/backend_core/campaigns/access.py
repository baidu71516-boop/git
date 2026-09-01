"""Department scope and mutation-actor checks shared by Phase 3A services."""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from backend_core.auth import BusinessAuthorizationContext
from backend_core.auth.enums import DepartmentStatus, OperatorStatus, Role
from backend_core.auth.repository import AuthRepository
from backend_core.campaigns.errors import CampaignOutreachError


@dataclass(frozen=True, slots=True)
class DepartmentScope:
    department_id: UUID
    cross_department_override: bool


class CampaignOutreachAccess:
    """Validate the frozen Department/RBAC boundary before touching domain rows."""

    def __init__(self, auth_repository: AuthRepository) -> None:
        self.auth_repository = auth_repository

    @staticmethod
    def own_department_id(context: BusinessAuthorizationContext) -> UUID:
        department_id = getattr(context.department, "id", None)
        if not isinstance(department_id, UUID):
            raise CampaignOutreachError(
                409,
                "DEPARTMENT_REQUIRED",
                "Authenticated department is required",
            )
        return department_id

    @staticmethod
    def require_mutation(context: BusinessAuthorizationContext) -> UUID:
        operator = context.operator
        operator_id = getattr(operator, "id", None)
        if not isinstance(operator_id, UUID):
            raise CampaignOutreachError(
                409,
                "OPERATOR_REQUIRED",
                "Select an operator first",
            )
        if context.effective_role is Role.VIEWER:
            raise CampaignOutreachError(403, "PERMISSION_DENIED", "Viewer role is read-only")
        return operator_id

    async def resolve_read_scope(
        self,
        context: BusinessAuthorizationContext,
        requested_department_id: UUID | None,
    ) -> DepartmentScope:
        own_department_id = self.own_department_id(context)
        target_department_id = requested_department_id or own_department_id
        if (
            target_department_id != own_department_id
            and context.effective_role is not Role.SUPER_ADMIN
        ):
            # Read operations are scoped in their repository query; this preserves
            # the frozen no-disclosure behavior for non-Super callers.
            raise CampaignOutreachError(404, "RESOURCE_NOT_FOUND", "Resource not found")
        if target_department_id != own_department_id:
            department = await self.auth_repository.get_department(target_department_id)
            if department is None or department.status is not DepartmentStatus.ACTIVE:
                raise CampaignOutreachError(404, "DEPARTMENT_NOT_FOUND", "Department not found")
        return DepartmentScope(
            department_id=target_department_id,
            cross_department_override=target_department_id != own_department_id,
        )

    async def resolve_mutation_scope(
        self,
        context: BusinessAuthorizationContext,
        requested_department_id: UUID | None,
    ) -> tuple[DepartmentScope, UUID]:
        operator_id = self.require_mutation(context)
        own_department_id = self.own_department_id(context)
        target_department_id = requested_department_id or own_department_id
        if (
            target_department_id != own_department_id
            and context.effective_role is not Role.SUPER_ADMIN
        ):
            # Match read scoping so a mutation cannot disclose another Department.
            raise CampaignOutreachError(
                404,
                "RESOURCE_NOT_FOUND",
                "Resource not found",
            )
        if target_department_id != own_department_id:
            department = await self.auth_repository.get_department(target_department_id)
            if department is None or department.status is not DepartmentStatus.ACTIVE:
                raise CampaignOutreachError(404, "DEPARTMENT_NOT_FOUND", "Department not found")
        return (
            DepartmentScope(
                department_id=target_department_id,
                cross_department_override=target_department_id != own_department_id,
            ),
            operator_id,
        )

    async def target_department_operator_id(
        self,
        *,
        context: BusinessAuthorizationContext,
        target_department_id: UUID,
        fallback_operator_id: UUID,
    ) -> UUID | None:
        """Return the selected actor only when it satisfies a target-side composite FK.

        Campaign/Member persistence uses Department-local actor foreign keys.  A
        cross-Department Super Admin action remains truthfully attributed in Audit,
        while a caller can choose an existing target Department owner when the
        immutable row itself needs a Department-local attribution value.
        """

        operator = context.operator
        if (
            getattr(operator, "id", None) == fallback_operator_id
            and getattr(operator, "department_id", None) == target_department_id
            and getattr(operator, "status", None) is OperatorStatus.ACTIVE
        ):
            return fallback_operator_id
        return None
