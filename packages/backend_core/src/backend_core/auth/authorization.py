"""Closed Permissions V1 authorization primitives.

This module deliberately has no persistence dependency. The auth service
reloads effective role and persisted grants for each Human business request;
these functions evaluate only the resulting closed authorization context.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from enum import StrEnum
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


class ModuleRequirementKind(StrEnum):
    """The closed ways a route can combine ModuleKey grants."""

    EXACT = "EXACT"
    ANY_OF = "ANY_OF"
    ALL_OF = "ALL_OF"


@dataclass(frozen=True, slots=True)
class ModuleRequirement:
    """One closed module requirement declared by one Human business route."""

    kind: ModuleRequirementKind
    modules: frozenset[ModuleKey]

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ModuleRequirementKind):
            raise TypeError("Module authorization requires a closed requirement kind")
        if not self.modules:
            raise ValueError("At least one closed ModuleKey is required")
        if not all(isinstance(module_key, ModuleKey) for module_key in self.modules):
            raise TypeError("Module authorization requires closed ModuleKey values")
        if self.kind is ModuleRequirementKind.EXACT and len(self.modules) != 1:
            raise ValueError("EXACT requires exactly one closed ModuleKey")


def EXACT(module_key: ModuleKey) -> ModuleRequirement:
    """Require one exact ModuleKey."""

    return ModuleRequirement(ModuleRequirementKind.EXACT, _closed_module_keys(module_key))


def ANY_OF(*module_keys: ModuleKey) -> ModuleRequirement:
    """Require at least one ModuleKey from a closed set."""

    return ModuleRequirement(ModuleRequirementKind.ANY_OF, _closed_module_keys(module_keys))


def ALL_OF(*module_keys: ModuleKey) -> ModuleRequirement:
    """Require every ModuleKey from a closed set."""

    return ModuleRequirement(ModuleRequirementKind.ALL_OF, _closed_module_keys(module_keys))


def role_is_within_department_ceiling(operator_role: Role, ceiling: Role) -> bool:
    """Return whether the stored Operator role is valid for its Department."""

    return role_at_or_below(operator_role, ceiling)


def viewer_mutation_allowed(role: Role) -> bool:
    """Viewer mutation access is unconditionally denied, regardless of module grants."""

    return role is not Role.VIEWER


def _closed_module_keys(module_keys: ModuleKey | Iterable[ModuleKey]) -> frozenset[ModuleKey]:
    if isinstance(module_keys, ModuleKey):
        return frozenset((module_keys,))
    resolved = frozenset(module_keys)
    if not resolved:
        raise ValueError("At least one closed ModuleKey is required")
    if not all(isinstance(module_key, ModuleKey) for module_key in resolved):
        raise TypeError("Module authorization requires closed ModuleKey values")
    return resolved


def _as_requirement(
    requirement: ModuleRequirement | ModuleKey | Iterable[ModuleKey],
) -> ModuleRequirement:
    """Retain one-module compatibility while all new declarations stay explicit."""

    if isinstance(requirement, ModuleRequirement):
        return requirement
    return ModuleRequirement(ModuleRequirementKind.EXACT, _closed_module_keys(requirement))


def _matches_requirement(
    authorized_modules: frozenset[ModuleKey],
    requirement: ModuleRequirement,
) -> bool:
    if requirement.kind is ModuleRequirementKind.EXACT:
        return requirement.modules <= authorized_modules
    if requirement.kind is ModuleRequirementKind.ANY_OF:
        return bool(requirement.modules & authorized_modules)
    if requirement.kind is ModuleRequirementKind.ALL_OF:
        return requirement.modules <= authorized_modules
    raise AssertionError(f"Unhandled requirement kind {requirement.kind!r}")


def require_module_read(
    context: EffectiveAuthorizationContext,
    requirement: ModuleRequirement | ModuleKey | Iterable[ModuleKey],
) -> EffectiveAuthorizationContext:
    """Authorize one closed requirement for a Human business read."""

    resolved_requirement = _as_requirement(requirement)
    if context.effective_role is Role.SUPER_ADMIN:
        return context
    # Admin is deliberately not a grantable module.  Preserve the existing
    # effective-Super-Admin-only failure rather than treating it as an ordinary
    # missing grant.
    if ModuleKey.ADMIN in resolved_requirement.modules:
        raise AuthError(403, "PERMISSION_DENIED", "Super admin permission required")
    if not _matches_requirement(context.authorized_modules, resolved_requirement):
        raise AuthError(403, "MODULE_ACCESS_DENIED", "Module access denied")
    return context


def require_module_mutation(
    context: EffectiveAuthorizationContext,
    requirement: ModuleRequirement | ModuleKey | Iterable[ModuleKey],
) -> EffectiveAuthorizationContext:
    """Authorize a future module mutation after the module predicate succeeds."""

    authorized = require_module_read(context, requirement)
    if not viewer_mutation_allowed(authorized.effective_role):
        raise AuthError(403, "PERMISSION_DENIED", "Viewer role is read-only")
    return authorized


__all__ = [
    "ALL_OF",
    "ANY_OF",
    "EXACT",
    "EffectiveAuthorizationContext",
    "ModuleKey",
    "ModuleRequirement",
    "ModuleRequirementKind",
    "ResolvedDepartmentScope",
    "require_module_mutation",
    "require_module_read",
    "role_is_within_department_ceiling",
    "viewer_mutation_allowed",
]
