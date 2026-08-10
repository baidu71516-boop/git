"""Department authentication and operator attribution."""

from backend_core.auth.enums import Role
from backend_core.auth.service import AuthContext, AuthError, AuthService

__all__ = ["AuthContext", "AuthError", "AuthService", "Role"]
