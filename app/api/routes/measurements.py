from fastapi import APIRouter

from app.api.deps import FileServiceDep, PageDep
from app.api.serializers import feature_measurement_response, summary_response
from app.schemas.common import ErrorResponse
from app.schemas.measurement import MeasurementReportResponse

router = APIRouter(prefix="/api/files", tags=["measurements"])


@router.get(
    "/{file_id}/measurements/",
    response_model=MeasurementReportResponse,
    summary="Get per-feature measurements",
    description=(
        "Area and perimeter for polygonal features, length for linear features, in metres. "
        "Every feature is listed with a `status`; features that could not be measured "
        "carry a `message` explaining why. The summary covers the whole file."
    ),
    responses={404: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
def get_measurements(
    file_id: str, service: FileServiceDep, page: PageDep
) -> MeasurementReportResponse:
    record, features, totals = service.list_measurements(file_id, page.limit, page.offset)
    return MeasurementReportResponse(
        file_id=record.id,
        source_crs=record.crs,
        summary=summary_response(record, totals),
        total=record.feature_count,
        limit=page.limit,
        offset=page.offset,
        items=[feature_measurement_response(feature) for feature in features],
    )
