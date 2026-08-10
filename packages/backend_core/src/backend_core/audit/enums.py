"""Audit actions and outcomes."""

from enum import StrEnum


class AuditAction(StrEnum):
    LOGIN_SUCCESS = "LOGIN_SUCCESS"
    LOGIN_FAILED = "LOGIN_FAILED"
    LOGIN_LOCKED = "LOGIN_LOCKED"
    LOGOUT = "LOGOUT"
    OPERATOR_SELECTED = "OPERATOR_SELECTED"
    PASSWORD_RESET = "PASSWORD_RESET"
    BOOTSTRAP_ADMIN = "BOOTSTRAP_ADMIN"


class AuditResult(StrEnum):
    SUCCESS = "success"
    FAILED = "failed"
    DENIED = "denied"
