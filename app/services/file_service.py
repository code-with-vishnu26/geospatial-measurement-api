"""Orchestrates the upload lifecycle: validate -> store -> process -> persist."""

import logging
import shutil
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import BinaryIO

from pyproj import CRS
from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.enums import FileStatus, FileType
from app.core.exceptions import FileNotProcessedError, NotFoundError, ProcessingError
from app.db.models import Feature, GeoFile
from app.db.repositories import (
    FeatureRepository,
    FileRepository,
    MeasurementTotals,
    NewFeature,
    NewMeasurement,
)
from app.services.crs_service import parse_crs
from app.services.measurement_service import describe_crs
from app.services.processing_service import ProcessingResult, ProcessingService
from app.utils.file_utils import (
    detect_file_type,
    sanitize_filename,
    save_stream,
    verify_signature,
)
from app.utils.geometry_utils import to_geojson
from app.utils.zip_utils import ArchiveLimits, inspect_shapefile_archive

logger = logging.getLogger(__name__)

_STORED_NAMES = {FileType.KML: "source.kml", FileType.SHAPEFILE: "source.zip"}


class FileService:
    def __init__(
        self, session: Session, settings: Settings, processing_service: ProcessingService
    ) -> None:
        self.session = session
        self.settings = settings
        self.processing_service = processing_service
        self.files = FileRepository(session)
        self.features = FeatureRepository(session)

    def upload(
        self, filename: str | None, stream: BinaryIO, assumed_crs: str | None = None
    ) -> GeoFile:
        """Validate, store and synchronously process an upload.

        Request-level problems (type, size, archive structure, CRS syntax) raise
        4xx errors before anything is persisted. Problems with the *content*
        are recorded on a FAILED file record and raised as ``ProcessingError``.
        """
        display_name = sanitize_filename(filename)
        file_type = detect_file_type(display_name)
        crs = parse_crs(assumed_crs) if assumed_crs else None

        file_id = uuid.uuid4().hex
        stored_path = self._store(file_id, file_type, stream)
        record = self.files.save(
            GeoFile(
                id=file_id,
                original_filename=display_name,
                stored_filename=stored_path.relative_to(self.settings.upload_dir).as_posix(),
                file_type=file_type,
                size_bytes=stored_path.stat().st_size,
                status=FileStatus.UPLOADED,
            )
        )
        logger.info(
            "File uploaded",
            extra={"file_id": file_id, "file_type": file_type, "size_bytes": record.size_bytes},
        )
        self._process(record, stored_path, crs)
        return record

    def get(self, file_id: str) -> GeoFile:
        record = self.files.get(file_id)
        if record is None:
            raise NotFoundError(f"File '{file_id}' was not found.")
        return record

    def list_page(self, limit: int, offset: int) -> tuple[list[GeoFile], int]:
        return self.files.list_page(limit, offset)

    def list_features(self, file_id: str, limit: int, offset: int) -> tuple[GeoFile, list[Feature]]:
        record = self._get_completed(file_id)
        return record, self.features.list_for_file(file_id, limit, offset)

    def list_measurements(
        self, file_id: str, limit: int, offset: int
    ) -> tuple[GeoFile, list[Feature], MeasurementTotals]:
        record, features = self.list_features(file_id, limit, offset)
        return record, features, self.features.measurement_totals(file_id)

    def _get_completed(self, file_id: str) -> GeoFile:
        record = self.get(file_id)
        if record.status is not FileStatus.COMPLETED:
            raise FileNotProcessedError(
                f"File '{file_id}' has status {record.status}; results are only "
                "available for COMPLETED files.",
                details={"file_id": file_id, "status": record.status},
            )
        return record

    def _store(self, file_id: str, file_type: FileType, stream: BinaryIO) -> Path:
        directory = self.settings.upload_dir / file_id
        directory.mkdir(parents=True, exist_ok=False)
        path = directory / _STORED_NAMES[file_type]
        try:
            save_stream(stream, path, self.settings.max_upload_bytes)
            verify_signature(path, file_type)
            if file_type is FileType.SHAPEFILE:
                inspect_shapefile_archive(path, self._archive_limits())
        except BaseException:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return path

    def _archive_limits(self) -> ArchiveLimits:
        return ArchiveLimits(
            max_members=self.settings.max_archive_members,
            max_extracted_bytes=self.settings.max_extracted_bytes,
            max_compression_ratio=self.settings.max_compression_ratio,
        )

    def _process(self, record: GeoFile, path: Path, assumed_crs: CRS | None) -> None:
        record.status = FileStatus.PROCESSING
        self.files.save(record)
        logger.info("Processing started", extra={"file_id": record.id})
        started = time.perf_counter()

        try:
            result = self.processing_service.process(path, record.file_type, assumed_crs)
            self._persist_result(record, result)
        except ProcessingError as exc:
            self._mark_failed(record, exc.message, started)
            logger.warning("Processing failed", extra={"file_id": record.id, "reason": exc.message})
            exc.details.setdefault("file_id", record.id)
            raise
        except Exception:
            self._mark_failed(record, "Unexpected error while processing the file.", started)
            logger.exception("Processing crashed", extra={"file_id": record.id})
            raise

        record.status = FileStatus.COMPLETED
        self._finish(record, started)
        logger.info(
            "Processing completed",
            extra={
                "file_id": record.id,
                "feature_count": record.feature_count,
                "duration_ms": record.processing_time_ms,
            },
        )

    def _persist_result(self, record: GeoFile, result: ProcessingResult) -> None:
        record.crs, record.crs_name = describe_crs(result.crs)
        record.warnings = result.warnings
        record.feature_count = len(result.features)
        source_crs = record.crs or ""
        self.files.replace_features(
            record.id,
            [
                NewFeature(
                    index=item.parsed.index,
                    source_id=item.parsed.source_id,
                    geometry_type=item.parsed.geometry_type,
                    geometry=to_geojson(item.parsed.geometry),
                    properties=item.parsed.properties,
                    measurement_status=item.measurement.status,
                    measurement_message=item.measurement.message,
                    measurements=[
                        NewMeasurement(
                            measurement_type=value.type,
                            value=value.value,
                            unit=value.unit,
                            method=value.method,
                            source_crs=source_crs,
                            measurement_crs=value.measurement_crs,
                        )
                        for value in item.measurement.values
                    ],
                )
                for item in result.features
            ],
        )

    def _mark_failed(self, record: GeoFile, message: str, started: float) -> None:
        self.session.rollback()
        record.status = FileStatus.FAILED
        record.error_message = message
        record.feature_count = 0
        self._finish(record, started)

    def _finish(self, record: GeoFile, started: float) -> None:
        record.processing_time_ms = round((time.perf_counter() - started) * 1000)
        record.processed_at = datetime.now(UTC)
        self.files.save(record)
