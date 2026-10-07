"""Per-feature measurement.

Measurement never fails a whole file: each feature receives a status
explaining whether (and why not) it was measured.
"""

import math
from dataclasses import dataclass, field

import shapely
from pyproj import CRS, Geod
from shapely.geometry.base import BaseGeometry

from app.core.enums import MeasurementMethod, MeasurementStatus, MeasurementType
from app.processors.base import ParsedFeature
from app.services.crs_service import (
    CRSService,
    MeasurementError,
    MeasurementPlan,
    crs_identifier,
    transform_geometry,
)
from app.utils.geometry_utils import GeometryKind, geometry_kind

GEODESIC_LABEL = "WGS 84 ellipsoid (geodesic)"
_GEOD = Geod(ellps="WGS84")
_DECIMALS = 3


@dataclass(frozen=True, slots=True)
class MeasurementValue:
    type: MeasurementType
    value: float
    unit: str
    method: MeasurementMethod
    measurement_crs: str


@dataclass(frozen=True, slots=True)
class FeatureMeasurement:
    status: MeasurementStatus
    message: str | None = None
    values: list[MeasurementValue] = field(default_factory=list)


class MeasurementService:
    def __init__(self, crs_service: CRSService) -> None:
        self.crs_service = crs_service

    def measure(self, feature: ParsedFeature, source_crs: CRS | None) -> FeatureMeasurement:
        if feature.parse_error:
            return FeatureMeasurement(MeasurementStatus.INVALID_GEOMETRY, feature.parse_error)

        geometry = feature.geometry
        if geometry is None:
            if feature.geometry_type:
                return FeatureMeasurement(
                    MeasurementStatus.UNSUPPORTED_GEOMETRY,
                    f"Geometry type '{feature.geometry_type}' is not supported.",
                )
            return FeatureMeasurement(MeasurementStatus.EMPTY_GEOMETRY, "Feature has no geometry.")
        if geometry.is_empty:
            return FeatureMeasurement(MeasurementStatus.EMPTY_GEOMETRY, "Geometry is empty.")

        kind = geometry_kind(geometry)
        if kind is GeometryKind.PUNTAL:
            return FeatureMeasurement(
                MeasurementStatus.NOT_APPLICABLE, "Points have no area or length."
            )
        if kind is GeometryKind.COLLECTION:
            return FeatureMeasurement(
                MeasurementStatus.UNSUPPORTED_GEOMETRY,
                f"{geometry.geom_type} may mix dimensions and is not measured; "
                "split it into single-type features.",
            )
        if not geometry.is_valid:
            return FeatureMeasurement(
                MeasurementStatus.INVALID_GEOMETRY,
                f"Invalid geometry: {shapely.is_valid_reason(geometry)}.",
            )
        if source_crs is None:
            return FeatureMeasurement(
                MeasurementStatus.UNKNOWN_CRS, "The file's CRS is unknown; cannot measure."
            )

        try:
            plan = self.crs_service.plan(geometry, source_crs)
            values = self._compute(geometry, kind, plan)
        except MeasurementError as exc:
            return FeatureMeasurement(MeasurementStatus.FAILED, str(exc))

        if not all(math.isfinite(v.value) for v in values):
            return FeatureMeasurement(
                MeasurementStatus.FAILED, "Measurement produced a non-finite value."
            )
        return FeatureMeasurement(MeasurementStatus.MEASURED, values=values)

    @staticmethod
    def _compute(
        geometry: BaseGeometry, kind: GeometryKind, plan: MeasurementPlan
    ) -> list[MeasurementValue]:
        projected = plan.project(geometry)
        factor = plan.unit_to_metre
        is_polygonal = kind is GeometryKind.POLYGONAL
        length_type = MeasurementType.PERIMETER if is_polygonal else MeasurementType.LENGTH

        if plan.geodesic_lengths:
            length = MeasurementValue(
                length_type,
                round(_geodesic_length(geometry, plan), _DECIMALS),
                "m",
                MeasurementMethod.GEODESIC,
                GEODESIC_LABEL,
            )
        else:
            length = MeasurementValue(
                length_type,
                round(projected.length * factor, _DECIMALS),
                "m",
                plan.method,
                plan.crs_label,
            )
        if not is_polygonal:
            return [length]

        area = MeasurementValue(
            MeasurementType.AREA,
            round(projected.area * factor**2, _DECIMALS),
            "m²",
            plan.method,
            plan.crs_label,
        )
        return [area, length]


def _geodesic_length(geometry: BaseGeometry, plan: MeasurementPlan) -> float:
    lonlat = shapely.force_2d(geometry)
    if plan.to_wgs84 is not None:
        lonlat = transform_geometry(lonlat, plan.to_wgs84)
    if geometry_kind(lonlat) is GeometryKind.POLYGONAL:
        # Geod.geometry_length only follows exterior rings; measure every ring so the
        # perimeter matches the projected definition (exterior + holes).
        lonlat = lonlat.boundary
    return _GEOD.geometry_length(lonlat)


def describe_crs(crs: CRS | None) -> tuple[str | None, str | None]:
    """Return ``(identifier, name)`` for persistence."""
    if crs is None:
        return None, None
    return crs_identifier(crs), crs.name
