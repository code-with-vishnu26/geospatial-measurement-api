from pathlib import Path

import pytest

from app.core.exceptions import ProcessingError
from app.processors.kml_processor import KMLProcessor
from tests.factories import (
    BLR_POLYGON_COORDS,
    kml_document,
    kml_line,
    kml_point,
    kml_polygon,
    placemark,
    sample_kml,
)


@pytest.fixture
def processor() -> KMLProcessor:
    return KMLProcessor(max_features=100)


def write(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "input.kml"
    path.write_text(content, encoding="utf-8")
    return path


def test_extracts_features_types_and_crs(processor: KMLProcessor, tmp_path: Path) -> None:
    dataset = processor.parse(write(tmp_path, sample_kml()))

    assert dataset.crs.to_epsg() == 4326
    assert [f.geometry_type for f in dataset.features] == ["Polygon", "LineString", "Point"]
    assert [f.index for f in dataset.features] == [0, 1, 2]
    assert dataset.features[0].source_id == "park-1"
    assert dataset.warnings == []


def test_preserves_name_description_and_extended_data(
    processor: KMLProcessor, tmp_path: Path
) -> None:
    extra = (
        "<description>Public park</description>"
        "<ExtendedData>"
        '<Data name="owner"><value>BBMP</value></Data>'
        '<Data name="name"><value>shadowed</value></Data>'
        '<SchemaData schemaUrl="#s"><SimpleData name="ward">42</SimpleData></SchemaData>'
        "</ExtendedData>"
    )
    content = kml_document(placemark(kml_point(1, 2), name="Park", extra=extra))

    properties = processor.parse(write(tmp_path, content)).features[0].properties

    assert properties == {
        "name": "Park",
        "description": "Public park",
        "folder": "Test",
        "owner": "BBMP",
        "data.name": "shadowed",  # colliding key is kept, not overwritten
        "ward": "42",
    }


def test_records_folder_hierarchy(processor: KMLProcessor, tmp_path: Path) -> None:
    content = kml_document(
        "<Folder><name>Sites</name><Folder><name>North</name>"
        + placemark(kml_point(1, 2))
        + "</Folder></Folder>"
    )
    feature = processor.parse(write(tmp_path, content)).features[0]
    assert feature.properties["folder"] == "Test/Sites/North"


def test_polygon_with_holes_and_altitude(processor: KMLProcessor, tmp_path: Path) -> None:
    outer = [(x, y, 10.0) for x, y in BLR_POLYGON_COORDS]
    hole = [(77.592, 12.972), (77.598, 12.972), (77.598, 12.978), (77.592, 12.972)]
    content = kml_document(placemark(kml_polygon(outer, hole)))

    geometry = processor.parse(write(tmp_path, content)).features[0].geometry

    assert len(geometry.interiors) == 1
    assert geometry.has_z


@pytest.mark.parametrize(
    ("parts", "expected"),
    [
        (kml_point(1, 2) + kml_point(3, 4), "MultiPoint"),
        (kml_line([(1, 2), (3, 4)]) + kml_line([(5, 6), (7, 8)]), "MultiLineString"),
        (kml_polygon(BLR_POLYGON_COORDS) * 2, "MultiPolygon"),
        (kml_point(1, 2) + kml_line([(1, 2), (3, 4)]), "GeometryCollection"),
    ],
)
def test_multigeometry_is_typed_by_content(
    processor: KMLProcessor, tmp_path: Path, parts: str, expected: str
) -> None:
    content = kml_document(placemark(f"<MultiGeometry>{parts}</MultiGeometry>"))
    assert processor.parse(write(tmp_path, content)).features[0].geometry_type == expected


def test_bad_feature_does_not_fail_the_file(processor: KMLProcessor, tmp_path: Path) -> None:
    content = kml_document(
        placemark("<LineString><coordinates>77.5,abc</coordinates></LineString>", name="bad"),
        placemark(
            "<Polygon><outerBoundaryIs><LinearRing><coordinates>1,2 3,4</coordinates>"
            "</LinearRing></outerBoundaryIs></Polygon>",
            name="too-few-points",
        ),
        placemark(kml_point(1, 2), name="good"),
    )
    features = processor.parse(write(tmp_path, content)).features

    assert features[0].geometry is None
    assert "Non-numeric" in features[0].parse_error
    assert features[1].geometry is None and features[1].parse_error
    assert features[2].geometry is not None


def test_unsupported_geometry_is_flagged_not_dropped(
    processor: KMLProcessor, tmp_path: Path
) -> None:
    content = kml_document(
        placemark("<Model><Location/></Model>", name="model"),
        placemark("", name="no-geometry"),
    )
    features = processor.parse(write(tmp_path, content)).features

    assert [(f.geometry, f.geometry_type) for f in features] == [(None, "Model"), (None, None)]


def test_coordinates_with_spaces_after_commas(processor: KMLProcessor, tmp_path: Path) -> None:
    content = kml_document(
        placemark(
            "<LineString><coordinates>\n  77.59, 12.97, 0\n\t77.60 ,12.98\n"
            "</coordinates></LineString>"
        )
    )
    feature = processor.parse(write(tmp_path, content)).features[0]
    assert feature.parse_error is None
    # Also mixes 3D and 2D tuples: the missing altitude defaults to 0.
    assert list(feature.geometry.coords) == [(77.59, 12.97, 0.0), (77.6, 12.98, 0.0)]


def test_hostile_folder_nesting_does_not_overflow_the_stack(
    processor: KMLProcessor, tmp_path: Path
) -> None:
    depth = 5_000
    content = kml_document("<Folder>" * depth + placemark(kml_point(1, 2)) + "</Folder>" * depth)
    assert len(processor.parse(write(tmp_path, content)).features) == 1


def test_hostile_multigeometry_nesting_is_flattened(
    processor: KMLProcessor, tmp_path: Path
) -> None:
    depth = 5_000
    nested = (
        "<MultiGeometry>" * depth + kml_point(1, 2) + kml_point(3, 4) + "</MultiGeometry>" * depth
    )
    feature = processor.parse(write(tmp_path, kml_document(placemark(nested)))).features[0]
    assert feature.geometry_type == "MultiPoint"
    assert len(feature.geometry.geoms) == 2


def test_placemark_order_follows_document_order(processor: KMLProcessor, tmp_path: Path) -> None:
    content = kml_document(
        placemark(kml_point(0, 0), name="a"),
        "<Folder><name>F</name>" + placemark(kml_point(1, 1), name="b") + "</Folder>",
        placemark(kml_point(2, 2), name="c"),
    )
    names = [f.properties["name"] for f in processor.parse(write(tmp_path, content)).features]
    assert names == ["a", "b", "c"]


def test_utf16_kml_is_parsed(processor: KMLProcessor, tmp_path: Path) -> None:
    document = kml_document(placemark(kml_point(1, 2))).replace("UTF-8", "UTF-16")
    path = tmp_path / "utf16.kml"
    path.write_bytes(document.encode("utf-16"))
    assert len(processor.parse(path).features) == 1


def test_kml_without_namespace_is_accepted(processor: KMLProcessor, tmp_path: Path) -> None:
    content = kml_document(placemark(kml_point(1, 2)), namespace="")
    assert len(processor.parse(write(tmp_path, content)).features) == 1


def test_empty_document_warns(processor: KMLProcessor, tmp_path: Path) -> None:
    dataset = processor.parse(write(tmp_path, kml_document()))
    assert dataset.features == []
    assert dataset.warnings


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("<kml><Document>", "not well-formed"),
        ("<gpx></gpx>", "root element must be <kml>"),
        (
            '<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY xxe SYSTEM "file:///etc/passwd">]>'
            "<kml><Document><name>&xxe;</name></Document></kml>",
            "forbidden XML",
        ),
        (
            '<?xml version="1.0"?><!DOCTYPE kml [<!ENTITY a "aaaaaaaaaa">'
            '<!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;&a;&a;">]><kml><Document><name>&b;</name>'
            "</Document></kml>",
            "forbidden XML",
        ),
    ],
)
def test_rejects_malformed_and_malicious_xml(
    processor: KMLProcessor, tmp_path: Path, content: str, message: str
) -> None:
    with pytest.raises(ProcessingError, match=message):
        processor.parse(write(tmp_path, content))


def test_feature_limit(tmp_path: Path) -> None:
    content = kml_document(*(placemark(kml_point(i, 0)) for i in range(3)))
    with pytest.raises(ProcessingError, match="maximum of 2 features"):
        KMLProcessor(max_features=2).parse(write(tmp_path, content))
