"""Department authentication and Permissions V1 authorization primitives."""

from backend_core.auth.authorization import (
    ALL_OF,
    ANY_OF,
    EXACT,
    EffectiveAuthorizationContext,
    ModuleKey,
    ModuleRequirement,
    ModuleRequirementKind,
    ResolvedDepartmentScope,
    require_module_mutation,
    require_module_read,
    role_is_within_department_ceiling,
    viewer_mutation_allowed,
)
from backend_core.auth.enums import Role, role_at_or_below
from backend_core.auth.errors import AuthError
from backend_core.auth.service import AuthContext, AuthService

type BusinessAuthorizationContext = AuthContext | EffectiveAuthorizationContext

__all__ = [
    "ALL_OF",
    "ANY_OF",
    "AuthContext",
    "AuthError",
    "AuthService",
    "BusinessAuthorizationContext",
    "EXACT",
    "EffectiveAuthorizationContext",
    "ModuleKey",
    "ModuleRequirement",
    "ModuleRequirementKind",
    "ResolvedDepartmentScope",
    "Role",
    "require_module_mutation",
    "require_module_read",
    "role_at_or_below",
    "role_is_within_department_ceiling",
    "viewer_mutation_allowed",
]
