"""Central Phase 1A authentication enums."""

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


ROLE_RANK: dict[Role, int] = {
    Role.VIEWER: 10,
    Role.OPERATOR: 20,
    Role.MANAGER: 30,
    Role.SUPER_ADMIN: 40,
}


def role_allows(current: Role, required: Role) -> bool:
    """Return whether a department-level role satisfies a minimum role."""

    return ROLE_RANK[current] >= ROLE_RANK[required]
