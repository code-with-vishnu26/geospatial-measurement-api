import pytest
from pyproj import CRS, Transformer
from shapely.geometry import LineString, box

from app.core.enums import MeasurementMethod
from app.core.exceptions import InvalidCRSError
from app.services.crs_service import (
    CRSService,
    MeasurementError,
    crs_identifier,
    horizontal_crs,
    parse_crs,
    utm_epsg,
)

ESRI_WGS84_WKT = (
    'GEOGCS["GCS_WGS_1984",DATUM["D_WGS_1984",SPHEROID["WGS_1984",6378137.0,298.257223563]],'
    'PRIMEM["Greenwich",0.0],UNIT["Degree",0.0174532925199433]]'
)


@pytest.fixture
def service() -> CRSService:
    return CRSService(max_scale_distortion=0.005)


def wgs84() -> CRS:
    return CRS.from_epsg(4326)


@pytest.mark.parametrize(
    ("lon", "lat", "expected"),
    [
        (77.59, 12.97, 32643),  # Bengaluru
        (151.21, -33.87, 32756),  # Sydney (southern hemisphere)
        (-74.0, 40.7, 32618),  # New York
        (0.0, 51.5, 32631),  # London, on a zone boundary
        (-180.0, 10.0, 32601),
        (180.0, 10.0, 32660),  # 180 is the same meridian as -180, but stays in zone 60
        (179.99, -10.0, 32760),
    ],
)
def test_utm_zone_selection(lon: float, lat: float, expected: int) -> None:
    assert utm_epsg(lon, lat) == expected


class TestCrsParsing:
    def test_identifier_prefers_epsg_code(self) -> None:
        assert crs_identifier(CRS.from_epsg(32643)) == "EPSG:32643"

    def test_esri_wkt_is_recognised_as_epsg_4326(self) -> None:
        assert crs_identifier(CRS.from_wkt(ESRI_WGS84_WKT)) == "EPSG:4326"

    @pytest.mark.parametrize("value", ["EPSG:4326", "epsg:32643", "4326", ESRI_WGS84_WKT])
    def test_parse_valid(self, value: str) -> None:
        assert parse_crs(value).is_geographic or parse_crs(value).is_projected

    @pytest.mark.parametrize("value", ["EPSG:999999", "not-a-crs", ""])
    def test_parse_invalid(self, value: str) -> None:
        with pytest.raises(InvalidCRSError):
            parse_crs(value)

    def test_geocentric_crs_rejected(self) -> None:
        with pytest.raises(InvalidCRSError, match="geographic or projected"):
            parse_crs("EPSG:4978")

    def test_compound_crs_uses_horizontal_part(self) -> None:
        assert horizontal_crs(CRS.from_user_input("EPSG:4326+5773")).to_epsg() == 4326


class TestMeasurementPlan:
    def test_geographic_input_is_projected_to_local_utm(self, service: CRSService) -> None:
        plan = service.plan(box(77.59, 12.97, 77.60, 12.98), wgs84())
        assert plan.method is MeasurementMethod.UTM
        assert plan.crs_label == "EPSG:32643"
        assert plan.transformer is not None

    def test_southern_hemisphere_uses_south_zone(self, service: CRSService) -> None:
        plan = service.plan(box(151.20, -33.88, 151.21, -33.87), wgs84())
        assert plan.crs_label == "EPSG:32756"

    def test_feature_crossing_zone_boundary_uses_zone_of_its_centre(
        self, service: CRSService
    ) -> None:
        # Zone 43/44 boundary is at 78E; this feature is centred at 78.2E.
        plan = service.plan(box(77.9, 12.9, 78.5, 13.1), wgs84())
        assert plan.method is MeasurementMethod.UTM
        assert plan.crs_label == "EPSG:32644"

    def test_low_distortion_projected_source_is_used_directly(self, service: CRSService) -> None:
        plan = service.plan(box(500_000, 1_430_000, 500_100, 1_430_100), CRS.from_epsg(32643))
        assert plan.method is MeasurementMethod.SOURCE_PROJECTED
        assert plan.transformer is None
        assert plan.unit_to_metre == 1.0

    def test_foot_based_crs_reports_unit_conversion(self, service: CRSService) -> None:
        # NY State Plane Long Island, US survey feet; coordinates near Manhattan.
        plan = service.plan(box(985_000, 200_000, 986_000, 201_000), CRS.from_epsg(2263))
        assert plan.method is MeasurementMethod.SOURCE_PROJECTED
        assert plan.unit_to_metre == pytest.approx(0.3048006096)

    def test_web_mercator_is_not_trusted_for_measurement(self, service: CRSService) -> None:
        to_mercator = Transformer.from_crs(4326, 3857, always_xy=True)
        x0, y0 = to_mercator.transform(10.0, 60.0)
        x1, y1 = to_mercator.transform(10.01, 60.01)
        plan = service.plan(box(x0, y0, x1, y1), CRS.from_epsg(3857))
        assert plan.method is MeasurementMethod.UTM
        assert plan.crs_label == "EPSG:32632"

    def test_projected_coordinates_far_outside_their_zone_are_reprojected(
        self, service: CRSService
    ) -> None:
        # Easting 0 in UTM 43N is ~4.5 degrees from the central meridian (>0.5% scale error).
        plan = service.plan(box(0, 1_430_000, 100, 1_430_100), CRS.from_epsg(32643))
        assert plan.method is MeasurementMethod.UTM
        assert plan.crs_label != "EPSG:32643"

    def test_continental_feature_falls_back_to_equal_area(self, service: CRSService) -> None:
        plan = service.plan(box(-10, 30, 30, 60), wgs84())
        assert plan.method is MeasurementMethod.LOCAL_EQUAL_AREA
        assert plan.geodesic_lengths is True
        assert "+proj=laea" in plan.crs_label

    def test_polar_feature_falls_back_to_equal_area(self, service: CRSService) -> None:
        plan = service.plan(box(10, 85, 11, 86), wgs84())
        assert plan.method is MeasurementMethod.LOCAL_EQUAL_AREA

    def test_antimeridian_crossing_is_rejected(self, service: CRSService) -> None:
        with pytest.raises(MeasurementError, match="antimeridian"):
            service.plan(LineString([(179.9, 0), (-179.9, 0)]), wgs84())

    def test_projected_source_crossing_antimeridian_is_rejected(self, service: CRSService) -> None:
        # EPSG:3832 (PDC Mercator) is Pacific-centred: x=0 is at 150E, so this
        # box spans 179E..179W in a single continuous projected extent.
        to_pdc = Transformer.from_crs(4326, 3832, always_xy=True)
        x0, y0 = to_pdc.transform(179.0, -17.0)
        x1, y1 = to_pdc.transform(-179.0, -16.0)
        with pytest.raises(MeasurementError, match="antimeridian"):
            service.plan(box(x0, y0, x1, y1), CRS.from_epsg(3832))

    def test_projected_coordinates_labelled_geographic_are_rejected(
        self, service: CRSService
    ) -> None:
        with pytest.raises(MeasurementError, match="declared CRS is probably wrong"):
            service.plan(box(500_000, 1_430_000, 500_100, 1_430_100), wgs84())

    def test_geographic_crs_in_grads_is_not_mistaken_for_out_of_range(
        self, service: CRSService
    ) -> None:
        # EPSG:4807 (NTF Paris) uses grads: latitude 90 gr = 81 degrees, which is valid.
        plan = service.plan(box(2.6, 90.0, 2.61, 90.01), CRS.from_epsg(4807))
        assert plan.method is MeasurementMethod.UTM

    def test_non_wgs84_geographic_crs_is_supported(self, service: CRSService) -> None:
        plan = service.plan(box(-77.04, 38.89, -77.03, 38.90), CRS.from_epsg(4269))  # NAD83
        assert plan.method is MeasurementMethod.UTM
        assert plan.crs_label == "EPSG:32618"
