"""Closed Permissions V1 authorization primitives.

This module deliberately has no grant repository. Until the later, frozen
module-grant migration exists, only Super Admin's implicit module access can
be resolved here; every other module request fails closed.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, replace
from uuid import UUID

from backend_core.auth.enums import ModuleKey, Role, role_at_or_below
from backend_core.auth.errors import AuthError
from backend_core.auth.models import AuthSession, Department, Operator


@dataclass(frozen=True, slots=True)
class ResolvedDepartmentScope:
    """The Department boundary resolved for one authorization decision."""

    department_id: UUID
    cross_department_override: bool


@dataclass(frozen=True, slots=True)
class EffectiveAuthorizationContext:
    """Immutable, current-state authorization input for future module guards."""

    department: Department
    operator: Operator
    department_role_ceiling: Role
    effective_role: Role
    department_scope: ResolvedDepartmentScope
    auth_session: AuthSession
    authorized_modules: frozenset[ModuleKey] = frozenset()


def role_is_within_department_ceiling(operator_role: Role, ceiling: Role) -> bool:
    """Return whether the stored Operator role is valid for its Department."""

    return role_at_or_below(operator_role, ceiling)


def viewer_mutation_allowed(role: Role) -> bool:
    """Viewer mutation access is unconditionally denied, regardless of module grants."""

    return role is not Role.VIEWER


def _closed_module_keys(
    module_keys: ModuleKey | Iterable[ModuleKey],
) -> frozenset[ModuleKey]:
    if isinstance(module_keys, ModuleKey):
        return frozenset((module_keys,))
    resolved = frozenset(module_keys)
    if not resolved:
        raise ValueError("At least one closed ModuleKey is required")
    if not all(isinstance(module_key, ModuleKey) for module_key in resolved):
        raise TypeError("Module authorization requires closed ModuleKey values")
    return resolved


def require_module_read(
    context: EffectiveAuthorizationContext,
    module_keys: ModuleKey | Iterable[ModuleKey],
) -> EffectiveAuthorizationContext:
    """Authorize an exact closed module set for a future read guard.

    Non-Super-Admin grants cannot be resolved before the frozen grant table is
    introduced, so they intentionally fail closed instead of inventing data.
    """

    required = _closed_module_keys(module_keys)
    if context.effective_role is not Role.SUPER_ADMIN:
        raise AuthError(403, "MODULE_ACCESS_DENIED", "Module access denied")
    return replace(context, authorized_modules=required)


def require_module_mutation(
    context: EffectiveAuthorizationContext,
    module_keys: ModuleKey | Iterable[ModuleKey],
) -> EffectiveAuthorizationContext:
    """Authorize a future module mutation after the module predicate succeeds."""

    authorized = require_module_read(context, module_keys)
    if not viewer_mutation_allowed(authorized.effective_role):
        raise AuthError(403, "PERMISSION_DENIED", "Viewer role is read-only")
    return authorized


__all__ = [
    "EffectiveAuthorizationContext",
    "ModuleKey",
    "ResolvedDepartmentScope",
    "require_module_mutation",
    "require_module_read",
    "role_is_within_department_ceiling",
    "viewer_mutation_allowed",
]
