from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.core.enums import FileStatus, FileType
from app.schemas.common import Pagination


class FileLinks(BaseModel):
    self: str
    features: str
    measurements: str


class FileResponse(BaseModel):
    model_config = ConfigDict(
        json_schema_extra={
            "example": {
                "id": "3f2a9c1e8b7d4e5fa6b7c8d9e0f1a2b3",
                "filename": "survey.kml",
                "file_type": "KML",
                "size_bytes": 20480,
                "status": "COMPLETED",
                "feature_count": 120,
                "crs": "EPSG:4326",
                "crs_name": "WGS 84",
                "warnings": [],
                "error_message": None,
                "processing_time_ms": 85,
                "created_at": "2026-10-07T10:15:30Z",
                "processed_at": "2026-10-07T10:15:30Z",
                "links": {
                    "self": "/api/files/3f2a9c1e8b7d4e5fa6b7c8d9e0f1a2b3/",
                    "features": "/api/files/3f2a9c1e8b7d4e5fa6b7c8d9e0f1a2b3/features/",
                    "measurements": "/api/files/3f2a9c1e8b7d4e5fa6b7c8d9e0f1a2b3/measurements/",
                },
            }
        }
    )

    id: str
    filename: str = Field(description="Sanitised original filename (display only).")
    file_type: FileType
    size_bytes: int
    status: FileStatus
    feature_count: int
    crs: str | None = Field(description="Source CRS identifier, null if the file declares none.")
    crs_name: str | None
    warnings: list[str]
    error_message: str | None = Field(description="Why processing failed (status FAILED).")
    processing_time_ms: int | None
    created_at: datetime
    processed_at: datetime | None
    links: FileLinks


class FileListResponse(Pagination):
    items: list[FileResponse]
