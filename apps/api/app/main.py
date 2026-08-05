"""Ponto de entrada ASGI da API."""

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .config import settings
from .exceptions import install_exception_handlers
from .routes.analyses import router as analyses_router
from .routes.health import router as health_router


def create_app() -> FastAPI:
    app = FastAPI(
        title="Motiva Vegetation Intelligence API",
        version=settings.version,
        description="API sincrona para validacao de AOI e monitoramento Sentinel-2.",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(analyses_router)
    install_exception_handlers(app)
    return app


app = create_app()
