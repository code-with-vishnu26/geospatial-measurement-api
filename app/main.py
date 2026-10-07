"""Application factory."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from app.api.routes import files, measurements
from app.core.config import MB, Settings, get_settings
from app.core.enums import FileType
from app.core.exceptions import AppError
from app.core.logging import configure_logging
from app.core.middleware import MaxBodySizeMiddleware, RequestLoggingMiddleware
from app.db.database import create_db_engine, create_session_factory, init_db
from app.processors.kml_processor import KMLProcessor
from app.processors.shapefile_processor import ShapefileProcessor
from app.services.crs_service import CRSService
from app.services.measurement_service import MeasurementService
from app.services.processing_service import ProcessingService
from app.utils.zip_utils import ArchiveLimits

logger = logging.getLogger(__name__)

# Allowance for multipart boundaries and form fields on top of the file itself.
MULTIPART_OVERHEAD_BYTES = 1 * MB

_HTTP_ERROR_CODES = {
    404: "NOT_FOUND",
    405: "METHOD_NOT_ALLOWED",
    413: "FILE_TOO_LARGE",
}


def build_processing_service(settings: Settings) -> ProcessingService:
    archive_limits = ArchiveLimits(
        max_members=settings.max_archive_members,
        max_extracted_bytes=settings.max_extracted_bytes,
        max_compression_ratio=settings.max_compression_ratio,
    )
    return ProcessingService(
        processors={
            FileType.KML: KMLProcessor(settings.max_features),
            FileType.SHAPEFILE: ShapefileProcessor(settings.max_features, archive_limits),
        },
        measurement_service=MeasurementService(CRSService(settings.max_scale_distortion)),
    )


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(settings.log_level)

    settings.upload_dir.mkdir(parents=True, exist_ok=True)
    engine = create_db_engine(settings.resolved_database_url)

    @asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        init_db(engine)
        logger.info("Application started", extra={"environment": settings.environment})
        yield
        engine.dispose()

    app = FastAPI(
        title=settings.app_name,
        version="1.0.0",
        description=(
            "Upload KML or zipped Shapefiles, extract their features and obtain "
            "CRS-correct area and length measurements in metres."
        ),
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.session_factory = create_session_factory(engine)
    app.state.processing_service = build_processing_service(settings)

    app.add_middleware(
        MaxBodySizeMiddleware,
        max_upload_bytes=settings.max_upload_bytes,
        overhead_bytes=MULTIPART_OVERHEAD_BYTES,
    )
    app.add_middleware(RequestLoggingMiddleware)

    _register_exception_handlers(app)
    app.include_router(files.router)
    app.include_router(measurements.router)

    @app.get("/health", tags=["health"], summary="Liveness check")
    def health() -> dict[str, str]:
        return {"status": "ok"}

    return app


def _error(status_code: int, code: str, message: str, details: Any = None) -> JSONResponse:
    return JSONResponse(
        status_code=status_code,
        content={"error": {"code": code, "message": message, "details": details or {}}},
    )


def _register_exception_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def handle_app_error(_: Request, exc: AppError) -> JSONResponse:
        return _error(exc.status_code, exc.code, exc.message, exc.details)

    @app.exception_handler(RequestValidationError)
    async def handle_validation_error(_: Request, exc: RequestValidationError) -> JSONResponse:
        errors = [
            {"location": list(e["loc"]), "message": e["msg"], "type": e["type"]}
            for e in exc.errors()
        ]
        return _error(422, "VALIDATION_ERROR", "Request validation failed.", {"errors": errors})

    @app.exception_handler(StarletteHTTPException)
    async def handle_http_error(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_ERROR_CODES.get(exc.status_code, "HTTP_ERROR")
        return _error(exc.status_code, code, str(exc.detail))

    @app.exception_handler(Exception)
    async def handle_unexpected(_: Request, exc: Exception) -> JSONResponse:
        logger.exception("Unhandled error", exc_info=exc)
        return _error(500, "INTERNAL_ERROR", "An unexpected error occurred.")
