"""ORM models.

Geometries are stored as GeoJSON in a JSON column so the schema runs on plain
SQLite. Moving to PostGIS means swapping that column for a ``geometry`` type;
nothing outside the repository layer depends on the storage format.
"""

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.enums import (
    FileStatus,
    FileType,
    MeasurementMethod,
    MeasurementStatus,
    MeasurementType,
)
from app.db.database import Base


class UTCDateTime(TypeDecorator[datetime]):
    """Timezone-aware UTC datetimes on every backend.

    SQLite has no timezone-aware type and returns naive values; this restores
    UTC on load so API timestamps are always unambiguous.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: object) -> datetime | None:
        return value.astimezone(UTC) if value is not None else None

    def process_result_value(self, value: datetime | None, dialect: object) -> datetime | None:
        if value is None or value.tzinfo is not None:
            return value
        return value.replace(tzinfo=UTC)


def _utcnow() -> datetime:
    return datetime.now(UTC)


def _new_id() -> str:
    return uuid.uuid4().hex


class GeoFile(Base):
    __tablename__ = "files"

    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=_new_id)
    original_filename: Mapped[str] = mapped_column(String(255))
    stored_filename: Mapped[str] = mapped_column(String(255))
    file_type: Mapped[FileType] = mapped_column(Enum(FileType, native_enum=False))
    size_bytes: Mapped[int] = mapped_column(Integer)
    status: Mapped[FileStatus] = mapped_column(
        Enum(FileStatus, native_enum=False), default=FileStatus.UPLOADED, index=True
    )
    crs: Mapped[str | None] = mapped_column(Text)
    crs_name: Mapped[str | None] = mapped_column(Text)
    feature_count: Mapped[int] = mapped_column(Integer, default=0)
    warnings: Mapped[list[str]] = mapped_column(JSON, default=list)
    error_message: Mapped[str | None] = mapped_column(Text)
    processing_time_ms: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=_utcnow)
    processed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)

    features: Mapped[list["Feature"]] = relationship(
        back_populates="file", cascade="all, delete-orphan", order_by="Feature.index"
    )


class Feature(Base):
    __tablename__ = "features"
    __table_args__ = (UniqueConstraint("file_id", "index", name="uq_feature_file_index"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    # Indexed through uq_feature_file_index (file_id, index), which also serves ordering.
    file_id: Mapped[str] = mapped_column(ForeignKey("files.id", ondelete="CASCADE"))
    index: Mapped[int] = mapped_column(Integer)
    source_id: Mapped[str | None] = mapped_column(Text)
    geometry_type: Mapped[str | None] = mapped_column(String(64))
    geometry: Mapped[dict[str, Any] | None] = mapped_column(JSON)
    properties: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    measurement_status: Mapped[MeasurementStatus] = mapped_column(
        Enum(MeasurementStatus, native_enum=False)
    )
    measurement_message: Mapped[str | None] = mapped_column(Text)

    file: Mapped[GeoFile] = relationship(back_populates="features")
    measurements: Mapped[list["Measurement"]] = relationship(
        back_populates="feature", cascade="all, delete-orphan", lazy="selectin"
    )


class Measurement(Base):
    __tablename__ = "measurements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    feature_id: Mapped[int] = mapped_column(
        ForeignKey("features.id", ondelete="CASCADE"), index=True
    )
    measurement_type: Mapped[MeasurementType] = mapped_column(
        Enum(MeasurementType, native_enum=False)
    )
    value: Mapped[float] = mapped_column(Float)
    unit: Mapped[str] = mapped_column(String(16))
    method: Mapped[MeasurementMethod] = mapped_column(Enum(MeasurementMethod, native_enum=False))
    source_crs: Mapped[str] = mapped_column(Text)
    measurement_crs: Mapped[str] = mapped_column(Text)

    feature: Mapped[Feature] = relationship(back_populates="measurements")
