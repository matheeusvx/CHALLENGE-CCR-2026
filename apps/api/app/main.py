"""Ponto de entrada ASGI da API."""

import logging

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.satellite_monitoring.database import init_database

from .config import settings
from .beta_auth import beta_auth_enabled, require_authenticated_account
from .exceptions import install_exception_handlers
from .routes.analyses import router as analyses_router
from .routes.auth import router as auth_router
from .routes.alerts import router as alerts_router
from .routes.guia import router as guia_router
from .routes.health import router as health_router
from .routes.validation import router as validation_router

logger = logging.getLogger(__name__)


def create_app() -> FastAPI:
    # O historico e opcional para a decisao: se o banco falhar, a analise
    # continua funcionando normalmente.
    try:
        init_database()
    except Exception:  # pragma: no cover
        logger.exception("Falha ao inicializar o banco do historico.")

    app = FastAPI(
        title="Motiva Vegetation Intelligence API",
        version=settings.version,
        description=(
            "API para validacao de AOI e monitoramento Sentinel-2, com execucao "
            "automatica experimental por viewport."
        ),
        docs_url=None if beta_auth_enabled() else "/docs",
        redoc_url=None if beta_auth_enabled() else "/redoc",
        openapi_url=None if beta_auth_enabled() else "/openapi.json",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    app.include_router(auth_router)
    app.include_router(analyses_router)
    app.include_router(alerts_router)
    operational_dependencies = (
        [Depends(require_authenticated_account)] if beta_auth_enabled() else []
    )
    app.include_router(guia_router, dependencies=operational_dependencies)
    app.include_router(validation_router, dependencies=operational_dependencies)
    install_exception_handlers(app)
    return app


app = create_app()
