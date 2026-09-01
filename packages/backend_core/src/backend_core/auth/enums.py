"""Central Phase 1A authentication enums."""

from collections.abc import Iterable
from enum import StrEnum


class DepartmentStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class OperatorStatus(StrEnum):
    ACTIVE = "active"
    DISABLED = "disabled"


class Role(StrEnum):
    SUPER_ADMIN = "super_admin"
    MANAGER = "manager"
    OPERATOR = "operator"
    VIEWER = "viewer"


class ModuleKey(StrEnum):
    """The closed Permissions V1 module-grant vocabulary."""

    TODAY_OUTREACH = "today_outreach"
    CAMPAIGNS = "campaigns"
    CANDIDATE_POOLS = "candidate_pools"
    INFLUENCER_LIBRARY = "influencer_library"
    DATA_COLLECTION = "data_collection"
    IMPORT_HISTORY = "import_history"
    DATA_UPDATES = "data_updates"
    ADMIN = "admin"


CANONICAL_MODULE_KEY_ORDER: tuple[ModuleKey, ...] = tuple(ModuleKey)
NON_ADMIN_MODULE_KEYS: tuple[ModuleKey, ...] = tuple(
    module_key for module_key in CANONICAL_MODULE_KEY_ORDER if module_key is not ModuleKey.ADMIN
)


ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 10,
    Role.OPERATOR: 20,
    Role.MANAGER: 30,
    Role.SUPER_ADMIN: 40,
}


def role_allows(current: Role, required: Role) -> bool:
    """Return whether a department-level role satisfies a minimum role."""

    return ROLE_RANK[current] >= ROLE_RANK[required]


def role_at_or_below(role: Role, ceiling: Role) -> bool:
    """Return whether a stored Operator role is valid under a Department ceiling."""

    return ROLE_RANK[role] <= ROLE_RANK[ceiling]


def canonical_module_keys(module_keys: object) -> tuple[ModuleKey, ...]:
    """Validate a complete grant set and return it in frozen module order."""

    if isinstance(module_keys, (str, bytes)):
        raise TypeError("Module grants require closed ModuleKey values")
    if not isinstance(module_keys, Iterable):
        raise TypeError("Module grants require an iterable of closed ModuleKey values")
    received = frozenset(module_keys)
    if not all(isinstance(module_key, ModuleKey) for module_key in received):
        raise TypeError("Module grants require closed ModuleKey values")
    return tuple(module_key for module_key in CANONICAL_MODULE_KEY_ORDER if module_key in received)
