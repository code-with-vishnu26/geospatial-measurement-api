from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import MeasurementMethod, MeasurementStatus, MeasurementType
from app.schemas.common import Pagination


class MeasurementValueResponse(BaseModel):
    type: MeasurementType
    value: float
    unit: str = Field(examples=["m²", "m"])
    method: MeasurementMethod
    measurement_crs: str = Field(description="CRS (or ellipsoid) the value was computed in.")


class FeatureMeasurementResponse(BaseModel):
    feature_index: int
    source_id: str | None
    geometry_type: str | None
    status: MeasurementStatus
    message: str | None = Field(description="Why the feature was not measured, if applicable.")
    measurements: list[MeasurementValueResponse]


class MeasurementSummary(BaseModel):
    feature_count: int
    status_counts: dict[MeasurementStatus, int]
    total_area_m2: float = Field(description="Sum of polygon areas across the whole file.")
    total_length_m: float = Field(description="Sum of line lengths across the whole file.")


class MeasurementReportResponse(Pagination):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "file_id": "3f2a9c1e8b7d4e5fa6b7c8d9e0f1a2b3",
                "source_crs": "EPSG:4326",
                "summary": {
                    "feature_count": 3,
                    "status_counts": {"MEASURED": 2, "NOT_APPLICABLE": 1},
                    "total_area_m2": 1201683.919,
                    "total_length_m": 1546.384,
                },
                "total": 3,
                "limit": 100,
                "offset": 0,
                "items": [
                    {
                        "feature_index": 0,
                        "source_id": "park-1",
                        "geometry_type": "Polygon",
                        "status": "MEASURED",
                        "message": None,
                        "measurements": [
                            {
                                "type": "area",
                                "value": 1201683.919,
                                "unit": "m²",
                                "method": "UTM",
                                "measurement_crs": "EPSG:32643",
                            },
                            {
                                "type": "perimeter",
                                "value": 4385.062,
                                "unit": "m",
                                "method": "UTM",
                                "measurement_crs": "EPSG:32643",
                            },
                        ],
                    }
                ],
            }
        }
    )

    file_id: str
    source_crs: str | None
    summary: MeasurementSummary
    items: list[FeatureMeasurementResponse]
