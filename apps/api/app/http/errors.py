"""Translate framework and unexpected errors into the API envelope."""

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from app.http.responses import envelope

logger = logging.getLogger(__name__)


class ApiError(Exception):
    """Expected HTTP-safe application error."""

    def __init__(self, status_code: int, code: str, message: str) -> None:
        self.status_code = status_code
        self.code = code
        self.message = message
        super().__init__(message)


def register_exception_handlers(app: FastAPI) -> None:
    """Install stable error response handlers."""

    @app.exception_handler(ApiError)
    async def api_error_handler(request: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=envelope(
                request,
                error={"code": exc.code, "message": exc.message, "details": None},
            ),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(
        request: Request,
        exc: RequestValidationError,
    ) -> JSONResponse:
        details = list(exc.errors())
        return JSONResponse(
            status_code=422,
            content=envelope(
                request,
                error={
                    "code": "VALIDATION_ERROR",
                    "message": "Request validation failed",
                    "details": details,
                },
            ),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(request: Request, exc: Exception) -> JSONResponse:
        logger.exception(
            "unhandled_request_error",
            extra={"request_id": request.state.request_id},
        )
        return JSONResponse(
            status_code=500,
            content=envelope(
                request,
                error={
                    "code": "INTERNAL_ERROR",
                    "message": "Internal server error",
                    "details": None,
                },
            ),
        )
