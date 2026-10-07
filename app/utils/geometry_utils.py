"""Geometry classification and JSON serialisation helpers."""

import json
import math
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum
from typing import Any

import numpy as np
import shapely
from shapely.geometry.base import BaseGeometry


class GeometryKind(StrEnum):
    PUNTAL = "PUNTAL"
    LINEAL = "LINEAL"
    POLYGONAL = "POLYGONAL"
    COLLECTION = "COLLECTION"


_KINDS = {
    "Point": GeometryKind.PUNTAL,
    "MultiPoint": GeometryKind.PUNTAL,
    "LineString": GeometryKind.LINEAL,
    "LinearRing": GeometryKind.LINEAL,
    "MultiLineString": GeometryKind.LINEAL,
    "Polygon": GeometryKind.POLYGONAL,
    "MultiPolygon": GeometryKind.POLYGONAL,
}


def geometry_kind(geometry: BaseGeometry) -> GeometryKind:
    return _KINDS.get(geometry.geom_type, GeometryKind.COLLECTION)


def to_geojson(geometry: BaseGeometry | None) -> dict[str, Any] | None:
    if geometry is None or geometry.is_empty:
        return None
    return json.loads(shapely.to_geojson(geometry))


def json_safe(value: Any) -> Any:
    """Convert attribute values (numpy/pandas/datetime types) into JSON-safe values."""
    if value is None or isinstance(value, (bool, str, int)):
        return value
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return json_safe(float(value))
    if isinstance(value, bytes):
        return value.decode("utf-8", errors="replace")
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple, np.ndarray)):
        return [json_safe(v) for v in value]
    if _is_missing(value):
        return None
    return str(value)


def _is_missing(value: Any) -> bool:
    try:
        return bool(value != value)  # NaN / NaT / pandas.NA are not equal to themselves
    except (TypeError, ValueError):
        return False
