"""Mapping from ORM objects to API response schemas."""

from app.core.enums import MeasurementStatus
from app.db.models import Feature, GeoFile
from app.db.repositories import MeasurementTotals
from app.schemas.feature import FeatureResponse
from app.schemas.file import FileLinks, FileResponse
from app.schemas.measurement import (
    FeatureMeasurementResponse,
    MeasurementSummary,
    MeasurementValueResponse,
)


def file_response(record: GeoFile) -> FileResponse:
    base = f"/api/files/{record.id}/"
    return FileResponse(
        id=record.id,
        filename=record.original_filename,
        file_type=record.file_type,
        size_bytes=record.size_bytes,
        status=record.status,
        feature_count=record.feature_count,
        crs=record.crs,
        crs_name=record.crs_name,
        warnings=record.warnings or [],
        error_message=record.error_message,
        processing_time_ms=record.processing_time_ms,
        created_at=record.created_at,
        processed_at=record.processed_at,
        links=FileLinks(
            self=base, features=f"{base}features/", measurements=f"{base}measurements/"
        ),
    )


def feature_response(feature: Feature, crs: str | None) -> FeatureResponse:
    return FeatureResponse(
        index=feature.index,
        source_id=feature.source_id,
        geometry_type=feature.geometry_type,
        geometry=feature.geometry,
        crs=crs,
        properties=feature.properties,
    )


def feature_measurement_response(feature: Feature) -> FeatureMeasurementResponse:
    return FeatureMeasurementResponse(
        feature_index=feature.index,
        source_id=feature.source_id,
        geometry_type=feature.geometry_type,
        status=feature.measurement_status,
        message=feature.measurement_message,
        measurements=[
            MeasurementValueResponse(
                type=m.measurement_type,
                value=m.value,
                unit=m.unit,
                method=m.method,
                measurement_crs=m.measurement_crs,
            )
            for m in feature.measurements
        ],
    )


def summary_response(record: GeoFile, totals: MeasurementTotals) -> MeasurementSummary:
    return MeasurementSummary(
        feature_count=record.feature_count,
        # Every status is always present, so clients can rely on the keys.
        status_counts={status: totals.status_counts.get(status, 0) for status in MeasurementStatus},
        total_area_m2=totals.total_area_m2,
        total_length_m=totals.total_length_m,
    )
