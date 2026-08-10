"""Import all model modules so Alembic sees complete metadata."""

from backend_core.audit.models import AuditLog
from backend_core.auth.models import AuthSession, Department, DepartmentPermission, Operator

__all__ = ["AuditLog", "AuthSession", "Department", "DepartmentPermission", "Operator"]
