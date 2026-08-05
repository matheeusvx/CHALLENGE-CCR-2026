"""Erros publicos e respostas de falha padronizadas."""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse


class ErrorCode(StrEnum):
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    INVALID_DATE_RANGE = "INVALID_DATE_RANGE"
    INVALID_PARAMETERS = "INVALID_PARAMETERS"
    SATELLITE_PROVIDER_ERROR = "SATELLITE_PROVIDER_ERROR"
    NO_SCENES_FOUND = "NO_SCENES_FOUND"
    INSUFFICIENT_OBSERVATIONS = "INSUFFICIENT_OBSERVATIONS"
    PROCESSING_ERROR = "PROCESSING_ERROR"
    ARTIFACT_NOT_FOUND = "ARTIFACT_NOT_FOUND"


class ApiError(Exception):
    def __init__(
        self,
        code: str,
        message: str,
        *,
        status_code: int = 400,
        details: list[Any] | None = None,
    ) -> None:
        super().__init__(message)
        self.code = code
        self.message = message
        self.status_code = status_code
        self.details = details or []


def error_payload(code: str, message: str, details: list[Any] | None = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": message, "details": details or []}}


def install_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def api_error_handler(_: Request, exc: ApiError) -> JSONResponse:
        return JSONResponse(
            status_code=exc.status_code,
            content=error_payload(exc.code, exc.message, exc.details),
        )

    @app.exception_handler(RequestValidationError)
    async def validation_error_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
        details = [
            {"location": list(error["loc"]), "message": error["msg"], "type": error["type"]}
            for error in exc.errors()
        ]
        return JSONResponse(
            status_code=422,
            content=error_payload(
                "INVALID_PARAMETERS",
                "Os parametros enviados nao sao validos.",
                details,
            ),
        )

    @app.exception_handler(Exception)
    async def unexpected_error_handler(_: Request, __: Exception) -> JSONResponse:
        return JSONResponse(
            status_code=500,
            content=error_payload(
                "PROCESSING_ERROR",
                "A analise nao pode ser concluida.",
            ),
        )
