from pathlib import Path

import pytest
from pyproj import CRS
from shapely.geometry import Polygon

from app.core.enums import FileType, MeasurementStatus
from app.processors.base import GeospatialProcessor, ParsedDataset, ParsedFeature
from app.services.crs_service import CRSService
from app.services.measurement_service import MeasurementService
from app.services.processing_service import ProcessingService
from tests.factories import BLR_POLYGON_COORDS


class StubProcessor(GeospatialProcessor):
    file_type = FileType.SHAPEFILE

    def __init__(self, crs: CRS | None) -> None:
        super().__init__(max_features=10)
        self.crs = crs

    def parse(self, path: Path) -> ParsedDataset:
        feature = ParsedFeature(0, Polygon(BLR_POLYGON_COORDS), "Polygon", {})
        return ParsedDataset(crs=self.crs, features=[feature], warnings=["original warning"])


def run(file_crs: CRS | None, assumed: CRS | None):
    service = ProcessingService(
        {FileType.SHAPEFILE: StubProcessor(file_crs)},
        MeasurementService(CRSService(max_scale_distortion=0.005)),
    )
    return service.process(Path("unused"), FileType.SHAPEFILE, assumed)


def test_unknown_crs_without_assumption_is_not_measured() -> None:
    result = run(None, None)
    assert result.crs is None
    assert result.features[0].measurement.status is MeasurementStatus.UNKNOWN_CRS


def test_assumed_crs_fills_missing_crs() -> None:
    result = run(None, CRS.from_epsg(4326))
    assert result.crs.to_epsg() == 4326
    assert result.features[0].measurement.status is MeasurementStatus.MEASURED
    assert result.warnings == ["The file declares no CRS; using assumed_crs EPSG:4326."]


def test_declared_crs_wins_over_conflicting_assumption() -> None:
    result = run(CRS.from_epsg(4326), CRS.from_epsg(32643))
    assert result.crs.to_epsg() == 4326
    assert any("was ignored" in w for w in result.warnings)


@pytest.mark.parametrize("assumed", [None, CRS.from_epsg(4326)])
def test_matching_or_absent_assumption_adds_no_warning(assumed: CRS | None) -> None:
    assert run(CRS.from_epsg(4326), assumed).warnings == ["original warning"]
