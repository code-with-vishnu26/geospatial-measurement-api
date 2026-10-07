import datetime
from pathlib import Path

import pytest
from shapely.geometry import LineString, Point, Polygon

from app.core.exceptions import ProcessingError
from app.processors.shapefile_processor import InvalidPrjError, ShapefileProcessor
from app.utils.zip_utils import ArchiveLimits
from tests.factories import BLR_POLYGON_COORDS, write_shapefile, zip_shapefile

LIMITS = ArchiveLimits(
    max_members=50, max_extracted_bytes=50 * 1024 * 1024, max_compression_ratio=200
)


@pytest.fixture
def processor() -> ShapefileProcessor:
    return ShapefileProcessor(max_features=100, archive_limits=LIMITS)


def make_zip(tmp_path: Path, payload: bytes) -> Path:
    path = tmp_path / "upload.zip"
    path.write_bytes(payload)
    return path


def test_reads_geometries_attributes_and_crs(processor: ShapefileProcessor, tmp_path: Path) -> None:
    shp = write_shapefile(
        tmp_path,
        [Polygon(BLR_POLYGON_COORDS), None],
        attributes={
            "name": ["park", "unknown"],
            "area_ha": [12.5, None],
            "surveyed": [datetime.date(2024, 5, 1), None],
        },
    )
    dataset = processor.parse(make_zip(tmp_path, zip_shapefile(shp)))

    assert dataset.crs.to_epsg() == 4326
    first, second = dataset.features
    assert first.geometry_type == "Polygon"
    assert first.properties == {"name": "park", "area_ha": 12.5, "surveyed": "2024-05-01"}
    assert first.source_id == "0"
    assert second.geometry is None
    assert second.properties == {"name": "unknown", "area_ha": None, "surveyed": None}


def test_detects_projected_crs(processor: ShapefileProcessor, tmp_path: Path) -> None:
    shp = write_shapefile(tmp_path, [Point(500_000, 1_430_000)], crs="EPSG:32643")
    dataset = processor.parse(make_zip(tmp_path, zip_shapefile(shp)))
    assert dataset.crs.to_epsg() == 32643


def test_reads_shapefile_inside_a_folder(processor: ShapefileProcessor, tmp_path: Path) -> None:
    shp = write_shapefile(tmp_path, [LineString([(0, 0), (1, 1)])])
    dataset = processor.parse(make_zip(tmp_path, zip_shapefile(shp, prefix="export/roads/")))
    assert dataset.features[0].geometry_type == "LineString"


def test_missing_prj_yields_unknown_crs_and_warning(
    processor: ShapefileProcessor, tmp_path: Path
) -> None:
    shp = write_shapefile(tmp_path, [Point(1, 2)])
    dataset = processor.parse(make_zip(tmp_path, zip_shapefile(shp, exclude=(".prj",))))
    assert dataset.crs is None
    assert ".prj" in dataset.warnings[0]


def test_invalid_prj_is_an_error_not_a_guess(processor: ShapefileProcessor, tmp_path: Path) -> None:
    shp = write_shapefile(tmp_path, [Point(1, 2)])
    payload = zip_shapefile(shp, prj_text="this is not a CRS")
    with pytest.raises(InvalidPrjError, match="valid coordinate reference system"):
        processor.parse(make_zip(tmp_path, payload))


def test_corrupt_shapefile_fails_without_leaking_paths(
    processor: ShapefileProcessor, tmp_path: Path
) -> None:
    shp = write_shapefile(tmp_path, [Point(1, 2)])
    shp.write_bytes(b"\x00" * 50)  # destroy the .shp header
    with pytest.raises(ProcessingError) as exc_info:
        processor.parse(make_zip(tmp_path, zip_shapefile(shp)))
    assert str(tmp_path) not in exc_info.value.message
    assert "could not be read" in exc_info.value.message


def test_empty_shapefile_warns(processor: ShapefileProcessor, tmp_path: Path) -> None:
    shp = write_shapefile(tmp_path, [], attributes={"name": []})
    dataset = processor.parse(make_zip(tmp_path, zip_shapefile(shp)))
    assert dataset.features == []
    assert "no features" in dataset.warnings[0]


def test_feature_limit_is_checked_before_loading_data(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shp = write_shapefile(tmp_path, [Point(i, 0) for i in range(3)])

    def fail_if_called(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the layer must not be loaded when it exceeds the limit")

    monkeypatch.setattr("app.processors.shapefile_processor.gpd.read_file", fail_if_called)
    with pytest.raises(ProcessingError, match="maximum of 2 features"):
        ShapefileProcessor(max_features=2, archive_limits=LIMITS).parse(
            make_zip(tmp_path, zip_shapefile(shp))
        )


def test_feature_limit(tmp_path: Path) -> None:
    shp = write_shapefile(tmp_path, [Point(i, 0) for i in range(3)])
    with pytest.raises(ProcessingError, match="maximum of 2 features"):
        ShapefileProcessor(max_features=2, archive_limits=LIMITS).parse(
            make_zip(tmp_path, zip_shapefile(shp))
        )
