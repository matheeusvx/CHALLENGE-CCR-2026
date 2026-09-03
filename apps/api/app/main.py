"""Ponto de entrada ASGI da API."""

import logging

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.satellite_monitoring.database import init_database

from .config import settings
from .exceptions import install_exception_handlers
from .routes.analyses import router as analyses_router
from .routes.guia import router as guia_router
from .routes.health import router as health_router

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
    app.include_router(guia_router)
    install_exception_handlers(app)
    return app


app = create_app()
