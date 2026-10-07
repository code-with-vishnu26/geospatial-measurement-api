from typing import Annotated

from fastapi import APIRouter, File, Form, Response, UploadFile, status

from app.api.deps import FileServiceDep, PageDep
from app.api.serializers import feature_response, file_response
from app.schemas.common import ErrorResponse
from app.schemas.feature import FeatureListResponse
from app.schemas.file import FileListResponse, FileResponse

router = APIRouter(prefix="/api/files", tags=["files"])


@router.post(
    "/",
    status_code=status.HTTP_201_CREATED,
    response_model=FileResponse,
    summary="Upload and process a geospatial file",
    responses={
        400: {"model": ErrorResponse, "description": "Invalid file, archive or CRS."},
        413: {"model": ErrorResponse, "description": "File exceeds the size limit."},
        422: {
            "model": ErrorResponse,
            "description": "File accepted but its content could not be processed. "
            "`error.details.file_id` references the FAILED file record.",
        },
    },
)
def upload_file(
    service: FileServiceDep,
    response: Response,
    file: Annotated[UploadFile, File(description="A `.kml` file or a `.zip` with one Shapefile.")],
    assumed_crs: Annotated[
        str | None,
        Form(
            description="CRS to use only when the file declares none (e.g. a Shapefile "
            "without .prj). Any pyproj-readable value, e.g. `EPSG:4326`.",
            examples=["EPSG:4326"],
        ),
    ] = None,
) -> FileResponse:
    record = service.upload(file.filename, file.file, assumed_crs or None)
    response.headers["Location"] = f"/api/files/{record.id}/"
    return file_response(record)


@router.get("/", response_model=FileListResponse, summary="List uploaded files")
def list_files(service: FileServiceDep, page: PageDep) -> FileListResponse:
    items, total = service.list_page(page.limit, page.offset)
    return FileListResponse(
        total=total,
        limit=page.limit,
        offset=page.offset,
        items=[file_response(item) for item in items],
    )


@router.get(
    "/{file_id}/",
    response_model=FileResponse,
    summary="Get file information and processing status",
    responses={404: {"model": ErrorResponse}},
)
def get_file(file_id: str, service: FileServiceDep) -> FileResponse:
    return file_response(service.get(file_id))


@router.get(
    "/{file_id}/features/",
    response_model=FeatureListResponse,
    summary="List extracted features (geometry, CRS, properties)",
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def list_features(file_id: str, service: FileServiceDep, page: PageDep) -> FeatureListResponse:
    record, features = service.list_features(file_id, page.limit, page.offset)
    return FeatureListResponse(
        file_id=record.id,
        crs=record.crs,
        total=record.feature_count,
        limit=page.limit,
        offset=page.offset,
        items=[feature_response(feature, record.crs) for feature in features],
    )
