from typing import Any

from fastapi import Request
from fastapi.responses import JSONResponse


class APIError(Exception):
    def __init__(self, status: int, code: str, message: str, details: Any = None, headers: dict | None = None):
        self.status, self.code, self.message = status, code, message
        self.details, self.headers = details, headers


def error_response(request: Request, error: APIError) -> JSONResponse:
    request_id = getattr(request.state, "request_id", None)
    headers = {"Cache-Control": "no-store", "X-Content-Type-Options": "nosniff", **(error.headers or {})}
    if request_id: headers["X-Request-ID"] = request_id
    return JSONResponse(status_code=error.status, headers=headers,
        content={"error": {"code": error.code, "message": error.message, "details": error.details},
                 "request_id": getattr(request.state, "request_id", None)})
