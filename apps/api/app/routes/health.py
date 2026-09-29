"""Healthcheck da API."""

from fastapi import APIRouter

from ..config import settings
from ..schemas import HealthResponse

router = APIRouter(prefix="/api", tags=["health"])


@router.get("/health", response_model=HealthResponse)
def healthcheck() -> HealthResponse:
    return HealthResponse(status="ok", service=settings.service_name, version=settings.version)
