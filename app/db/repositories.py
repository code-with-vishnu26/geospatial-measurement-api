"""Data access. All SQL lives here; services never build queries."""

from dataclasses import asdict, dataclass, field
from typing import Any

from sqlalchemy import ColumnElement, case, delete, func, insert, select
from sqlalchemy.orm import Session

from app.core.enums import MeasurementMethod, MeasurementStatus, MeasurementType
from app.db.models import Feature, GeoFile, Measurement


@dataclass(frozen=True, slots=True)
class NewMeasurement:
    measurement_type: MeasurementType
    value: float
    unit: str
    method: MeasurementMethod
    source_crs: str
    measurement_crs: str


@dataclass(frozen=True, slots=True)
class NewFeature:
    index: int
    source_id: str | None
    geometry_type: str | None
    geometry: dict[str, Any] | None
    properties: dict[str, Any]
    measurement_status: MeasurementStatus
    measurement_message: str | None
    measurements: list[NewMeasurement]


@dataclass(frozen=True, slots=True)
class MeasurementTotals:
    status_counts: dict[MeasurementStatus, int] = field(default_factory=dict)
    total_area_m2: float = 0.0
    total_length_m: float = 0.0


class FileRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, geo_file: GeoFile) -> GeoFile:
        self.session.add(geo_file)
        self.session.commit()
        return geo_file

    def get(self, file_id: str) -> GeoFile | None:
        return self.session.get(GeoFile, file_id)

    def list_page(self, limit: int, offset: int) -> tuple[list[GeoFile], int]:
        total = self.session.scalar(select(func.count()).select_from(GeoFile)) or 0
        items = self.session.scalars(
            select(GeoFile).order_by(GeoFile.created_at.desc()).limit(limit).offset(offset)
        ).all()
        return list(items), total

    def replace_features(self, file_id: str, features: list[NewFeature]) -> None:
        """Store processed features (idempotent: previous results are discarded).

        Uses bulk Core inserts: building one ORM object per row made persistence
        dominate processing time and scale super-linearly with feature count.
        """
        self.session.execute(delete(Feature).where(Feature.file_id == file_id))
        if not features:
            return

        feature_rows = [
            {"file_id": file_id, **{k: v for k, v in asdict(f).items() if k != "measurements"}}
            for f in features
        ]
        feature_ids = self.session.scalars(
            insert(Feature).returning(Feature.id, sort_by_parameter_order=True), feature_rows
        ).all()

        measurement_rows = [
            {"feature_id": feature_id, **asdict(m)}
            for feature_id, feature in zip(feature_ids, features, strict=True)
            for m in feature.measurements
        ]
        if measurement_rows:
            self.session.execute(insert(Measurement), measurement_rows)


class FeatureRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def list_for_file(self, file_id: str, limit: int, offset: int) -> list[Feature]:
        """Features with their measurements eagerly loaded (2 queries per page)."""
        return list(
            self.session.scalars(
                select(Feature)
                .where(Feature.file_id == file_id)
                .order_by(Feature.index)
                .limit(limit)
                .offset(offset)
            ).all()
        )

    def measurement_totals(self, file_id: str) -> MeasurementTotals:
        rows = self.session.execute(
            select(Feature.measurement_status, func.count())
            .where(Feature.file_id == file_id)
            .group_by(Feature.measurement_status)
        ).all()

        area, length = self.session.execute(
            select(
                _sum_of(MeasurementType.AREA),
                _sum_of(MeasurementType.LENGTH),
            )
            .select_from(Measurement)
            .join(Feature, Measurement.feature_id == Feature.id)
            .where(Feature.file_id == file_id)
        ).one()
        return MeasurementTotals(
            status_counts={MeasurementStatus(status): count for status, count in rows},
            total_area_m2=round(area, 3),
            total_length_m=round(length, 3),
        )


def _sum_of(measurement_type: MeasurementType) -> ColumnElement[float]:
    value = case((Measurement.measurement_type == measurement_type, Measurement.value))
    return func.coalesce(func.sum(value), 0.0)
