"""Measurement accuracy is verified against an independent ground truth:
geodesic area/length on the WGS 84 ellipsoid (pyproj.Geod)."""

import pytest
from pyproj import CRS, Transformer
from shapely.geometry import (
    GeometryCollection,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
    box,
)
from shapely.geometry.base import BaseGeometry

from app.core.enums import MeasurementMethod, MeasurementStatus, MeasurementType
from app.processors.base import ParsedFeature
from app.services.crs_service import CRSService
from app.services.measurement_service import FeatureMeasurement, MeasurementService
from tests.factories import (
    BLR_LINE_COORDS,
    BLR_POLYGON_COORDS,
    geodesic_area,
    geodesic_circle,
    geodesic_length,
    relative_error,
)

WGS84 = CRS.from_epsg(4326)
UTM_TOLERANCE = 0.005  # matches the configured max_scale_distortion
FALLBACK_TOLERANCE = 0.0005


@pytest.fixture
def service() -> MeasurementService:
    return MeasurementService(CRSService(max_scale_distortion=0.005))


def measure(
    service: MeasurementService, geometry: BaseGeometry | None, crs: CRS | None = WGS84
) -> FeatureMeasurement:
    geometry_type = geometry.geom_type if geometry is not None else None
    return service.measure(ParsedFeature(0, geometry, geometry_type, {}), crs)


def value_of(result: FeatureMeasurement, kind: MeasurementType) -> float:
    return next(v.value for v in result.values if v.type is kind)


class TestPolygon:
    def test_area_matches_geodesic_truth(self, service: MeasurementService) -> None:
        polygon = Polygon(BLR_POLYGON_COORDS)
        result = measure(service, polygon)

        assert result.status is MeasurementStatus.MEASURED
        area = value_of(result, MeasurementType.AREA)
        assert relative_error(area, geodesic_area(polygon)) < UTM_TOLERANCE
        assert (
            relative_error(value_of(result, MeasurementType.PERIMETER), geodesic_length(polygon))
            < UTM_TOLERANCE
        )

    def test_area_is_not_computed_in_degrees(self, service: MeasurementService) -> None:
        polygon = Polygon(BLR_POLYGON_COORDS)
        result = measure(service, polygon)
        assert polygon.area == pytest.approx(1e-4)  # "square degrees" - meaningless
        assert value_of(result, MeasurementType.AREA) > 1_000_000  # ~1.2 km²

    def test_units_and_method_are_reported(self, service: MeasurementService) -> None:
        result = measure(service, Polygon(BLR_POLYGON_COORDS))
        area = next(v for v in result.values if v.type is MeasurementType.AREA)
        assert area.unit == "m²"
        assert area.method is MeasurementMethod.UTM
        assert area.measurement_crs == "EPSG:32643"

    def test_hole_is_subtracted(self, service: MeasurementService) -> None:
        outer = box(77.59, 12.97, 77.60, 12.98)
        with_hole = Polygon(
            outer.exterior.coords, [box(77.592, 12.972, 77.598, 12.978).exterior.coords]
        )
        full = value_of(measure(service, outer), MeasurementType.AREA)
        holed = value_of(measure(service, with_hole), MeasurementType.AREA)
        assert relative_error(holed, geodesic_area(with_hole)) < UTM_TOLERANCE
        assert holed < full

    def test_multipolygon_area_is_sum_of_parts(self, service: MeasurementService) -> None:
        a, b = box(77.59, 12.97, 77.60, 12.98), box(77.62, 12.97, 77.63, 12.98)
        total = value_of(measure(service, MultiPolygon([a, b])), MeasurementType.AREA)
        parts = sum(value_of(measure(service, p), MeasurementType.AREA) for p in (a, b))
        assert total == pytest.approx(parts, rel=1e-6)

    def test_projected_source_square_is_exact(self, service: MeasurementService) -> None:
        square = box(500_000, 1_430_000, 500_100, 1_430_100)  # 100 m x 100 m in UTM 43N
        result = measure(service, square, CRS.from_epsg(32643))
        assert value_of(result, MeasurementType.AREA) == pytest.approx(10_000)
        assert value_of(result, MeasurementType.PERIMETER) == pytest.approx(400)

    def test_foot_units_are_converted_to_metres(self, service: MeasurementService) -> None:
        square = box(985_000, 200_000, 986_000, 201_000)  # 1000 ft x 1000 ft
        result = measure(service, square, CRS.from_epsg(2263))
        assert value_of(result, MeasurementType.AREA) == pytest.approx(
            1_000_000 * 0.3048006096**2, rel=1e-6
        )

    def test_web_mercator_distortion_is_corrected(self, service: MeasurementService) -> None:
        to_mercator = Transformer.from_crs(4326, 3857, always_xy=True)
        lonlat = box(10.0, 60.0, 10.01, 60.01)
        mercator = Polygon([to_mercator.transform(x, y) for x, y in lonlat.exterior.coords])

        area = value_of(measure(service, mercator, CRS.from_epsg(3857)), MeasurementType.AREA)

        assert mercator.area / geodesic_area(lonlat) > 3.9  # naive Web Mercator area is ~4x
        assert relative_error(area, geodesic_area(lonlat)) < UTM_TOLERANCE

    def test_very_large_polygon_uses_equal_area_fallback(self, service: MeasurementService) -> None:
        circle = geodesic_circle(lon=20.0, lat=50.0, radius_m=1_500_000)
        result = measure(service, circle)

        area = next(v for v in result.values if v.type is MeasurementType.AREA)
        perimeter = next(v for v in result.values if v.type is MeasurementType.PERIMETER)
        assert area.method is MeasurementMethod.LOCAL_EQUAL_AREA
        assert perimeter.method is MeasurementMethod.GEODESIC
        assert relative_error(area.value, geodesic_area(circle)) < FALLBACK_TOLERANCE
        assert relative_error(perimeter.value, geodesic_length(circle)) < FALLBACK_TOLERANCE

    def test_geodesic_perimeter_includes_holes_like_projected_perimeter(
        self, service: MeasurementService
    ) -> None:
        # Regression: Geod.geometry_length only follows the exterior ring.
        outer = geodesic_circle(lon=20.0, lat=50.0, radius_m=1_500_000)
        hole = geodesic_circle(lon=20.0, lat=50.0, radius_m=500_000)
        with_hole = Polygon(outer.exterior.coords, [hole.exterior.coords])

        result = measure(service, with_hole)
        perimeter = next(v for v in result.values if v.type is MeasurementType.PERIMETER)

        assert perimeter.method is MeasurementMethod.GEODESIC
        expected = geodesic_length(outer.exterior) + geodesic_length(hole.exterior)
        assert relative_error(perimeter.value, expected) < FALLBACK_TOLERANCE

    def test_z_coordinates_are_ignored(self, service: MeasurementService) -> None:
        flat = Polygon(BLR_POLYGON_COORDS)
        raised = Polygon([(x, y, 900.0) for x, y in BLR_POLYGON_COORDS])
        assert measure(service, raised).values == measure(service, flat).values


class TestLineString:
    def test_length_matches_geodesic_truth(self, service: MeasurementService) -> None:
        line = LineString(BLR_LINE_COORDS)
        result = measure(service, line)
        assert result.status is MeasurementStatus.MEASURED
        assert [v.type for v in result.values] == [MeasurementType.LENGTH]
        assert (
            relative_error(value_of(result, MeasurementType.LENGTH), geodesic_length(line))
            < UTM_TOLERANCE
        )

    def test_multilinestring_length_is_sum(self, service: MeasurementService) -> None:
        a, b = LineString(BLR_LINE_COORDS[:2]), LineString(BLR_LINE_COORDS[1:])
        total = value_of(measure(service, MultiLineString([a, b])), MeasurementType.LENGTH)
        assert relative_error(total, geodesic_length(a) + geodesic_length(b)) < UTM_TOLERANCE

    def test_long_line_is_measured_geodesically(self, service: MeasurementService) -> None:
        line = LineString([(-0.1278, 51.5074), (37.6173, 55.7558)])  # London -> Moscow
        result = measure(service, line)
        length = result.values[0]
        assert length.method is MeasurementMethod.GEODESIC
        assert length.value == pytest.approx(geodesic_length(line), rel=1e-9)


class TestNonMeasurableFeatures:
    @pytest.mark.parametrize("geometry", [Point(77.59, 12.97), MultiPoint([(1, 2), (3, 4)])])
    def test_points_are_not_applicable(
        self, service: MeasurementService, geometry: BaseGeometry
    ) -> None:
        result = measure(service, geometry)
        assert result.status is MeasurementStatus.NOT_APPLICABLE
        assert result.values == []

    def test_points_do_not_need_a_crs(self, service: MeasurementService) -> None:
        assert measure(service, Point(1, 2), crs=None).status is MeasurementStatus.NOT_APPLICABLE

    def test_geometry_collection_is_unsupported(self, service: MeasurementService) -> None:
        collection = GeometryCollection([Point(1, 2), LineString([(1, 2), (3, 4)])])
        result = measure(service, collection)
        assert result.status is MeasurementStatus.UNSUPPORTED_GEOMETRY
        assert "GeometryCollection" in result.message

    def test_self_intersecting_polygon_is_invalid(self, service: MeasurementService) -> None:
        bowtie = Polygon([(0, 0), (1, 1), (1, 0), (0, 1), (0, 0)])
        result = measure(service, bowtie)
        assert result.status is MeasurementStatus.INVALID_GEOMETRY
        assert "Self-intersection" in result.message

    def test_empty_geometry(self, service: MeasurementService) -> None:
        assert measure(service, Polygon()).status is MeasurementStatus.EMPTY_GEOMETRY

    def test_missing_geometry(self, service: MeasurementService) -> None:
        assert measure(service, None).status is MeasurementStatus.EMPTY_GEOMETRY

    def test_declared_but_unsupported_geometry(self, service: MeasurementService) -> None:
        result = service.measure(ParsedFeature(0, None, "Track", {}), WGS84)
        assert result.status is MeasurementStatus.UNSUPPORTED_GEOMETRY

    def test_unparseable_geometry_is_invalid(self, service: MeasurementService) -> None:
        feature = ParsedFeature(0, None, "LineString", {}, parse_error="Malformed coordinate")
        result = service.measure(feature, WGS84)
        assert result.status is MeasurementStatus.INVALID_GEOMETRY
        assert result.message == "Malformed coordinate"

    def test_unknown_crs_is_not_guessed(self, service: MeasurementService) -> None:
        result = measure(service, Polygon(BLR_POLYGON_COORDS), crs=None)
        assert result.status is MeasurementStatus.UNKNOWN_CRS
        assert result.values == []

    def test_antimeridian_crossing_fails_with_reason(self, service: MeasurementService) -> None:
        result = measure(service, LineString([(179.9, 0), (-179.9, 0)]))
        assert result.status is MeasurementStatus.FAILED
        assert "antimeridian" in result.message
