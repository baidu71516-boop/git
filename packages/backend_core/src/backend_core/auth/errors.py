"""Stable authentication and authorization failures."""


class AuthError(Exception):
    """A safe error that the HTTP layer can expose in its standard envelope."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)
