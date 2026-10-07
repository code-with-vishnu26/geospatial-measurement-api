from typing import Any

from pydantic import BaseModel, Field

from app.schemas.common import Pagination


class FeatureResponse(BaseModel):
    index: int = Field(description="0-based position of the feature in the file.")
    source_id: str | None = Field(
        description="Identifier from the source (KML Placemark id, Shapefile FID)."
    )
    geometry_type: str | None
    geometry: dict[str, Any] | None = Field(
        description="GeoJSON geometry, in the file's source CRS (see `crs`)."
    )
    crs: str | None
    properties: dict[str, Any]


class FeatureListResponse(Pagination):
    file_id: str
    crs: str | None
    items: list[FeatureResponse]
