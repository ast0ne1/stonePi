"""Device-facing errors in PROTOCOL.md §3 shape: {error, message, retry_after_s?}."""

from __future__ import annotations

from fastapi import Request
from fastapi.responses import JSONResponse


class ApiError(Exception):
    def __init__(self, status: int, error: str, message: str, retry_after: int | None = None):
        super().__init__(message)
        self.status, self.error, self.message, self.retry_after = status, error, message, retry_after


def api_error_response(_request: Request, exc: ApiError) -> JSONResponse:
    body: dict = {"error": exc.error, "message": exc.message}
    headers = {}
    if exc.retry_after:
        body["retry_after_s"] = exc.retry_after
        headers["Retry-After"] = str(exc.retry_after)
    return JSONResponse(body, status_code=exc.status, headers=headers)


UNAUTHORIZED = ApiError(401, "unauthorized", "Pair Pebble again")
NOT_FOUND = ApiError(404, "not_found", "Not found")
