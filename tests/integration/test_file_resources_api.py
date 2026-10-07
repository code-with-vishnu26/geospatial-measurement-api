from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from shapely.geometry import LineString, Point, Polygon
from shapely.geometry.base import BaseGeometry

from tests.factories import (
    BLR_LINE_COORDS,
    BLR_POLYGON_COORDS,
    geodesic_area,
    geodesic_length,
    kml_document,
    kml_point,
    placemark,
    relative_error,
    sample_kml,
    write_shapefile,
    zip_shapefile,
)


def upload_kml(client: TestClient, content: str) -> str:
    response = client.post("/api/files/", files={"file": ("f.kml", content.encode(), "text/xml")})
    assert response.status_code == 201, response.text
    return response.json()["id"]


class TestFileDetails:
    def test_get_file(self, client: TestClient) -> None:
        file_id = upload_kml(client, sample_kml())
        body = client.get(f"/api/files/{file_id}/").json()

        assert body["id"] == file_id
        assert body["status"] == "COMPLETED"
        assert body["feature_count"] == 3
        assert body["crs"] == "EPSG:4326"
        assert body["created_at"] and body["processed_at"]
        assert body["processing_time_ms"] >= 0

    def test_timestamps_stay_timezone_aware_after_reload(self, client: TestClient) -> None:
        # SQLite drops tzinfo; a fresh request must still return explicit UTC.
        file_id = upload_kml(client, sample_kml())
        body = client.get(f"/api/files/{file_id}/").json()
        assert body["created_at"].endswith("Z")
        assert body["processed_at"].endswith("Z")

    def test_nonexistent_file(self, client: TestClient) -> None:
        response = client.get("/api/files/does-not-exist/")
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "NOT_FOUND"

    @pytest.mark.parametrize("suffix", ["features/", "measurements/"])
    def test_sub_resources_of_nonexistent_file(self, client: TestClient, suffix: str) -> None:
        assert client.get(f"/api/files/does-not-exist/{suffix}").status_code == 404

    @pytest.mark.parametrize("suffix", ["features/", "measurements/"])
    def test_results_of_failed_file_conflict(self, client: TestClient, suffix: str) -> None:
        response = client.post("/api/files/", files={"file": ("x.kml", b"<kml>", "text/xml")})
        file_id = response.json()["error"]["details"]["file_id"]

        result = client.get(f"/api/files/{file_id}/{suffix}")
        assert result.status_code == 409
        assert result.json()["error"]["details"]["status"] == "FAILED"


class TestFeatures:
    def test_features_expose_required_fields(self, client: TestClient) -> None:
        file_id = upload_kml(client, sample_kml())
        body = client.get(f"/api/files/{file_id}/features/").json()

        assert body["total"] == 3
        polygon = body["items"][0]
        assert polygon["index"] == 0
        assert polygon["source_id"] == "park-1"
        assert polygon["geometry_type"] == "Polygon"
        assert polygon["geometry"]["type"] == "Polygon"
        assert polygon["geometry"]["coordinates"][0][0] == [77.59, 12.97]
        assert polygon["crs"] == "EPSG:4326"
        assert polygon["properties"] == {"name": "Park", "folder": "Test", "owner": "BBMP"}

    def test_feature_pagination(self, client: TestClient) -> None:
        file_id = upload_kml(client, kml_document(*(placemark(kml_point(i, 0)) for i in range(5))))
        body = client.get(f"/api/files/{file_id}/features/?limit=2&offset=3").json()
        assert body["total"] == 5
        assert [f["index"] for f in body["items"]] == [3, 4]

    @pytest.mark.parametrize("query", ["limit=0", "limit=1001", "offset=-1", "limit=abc"])
    def test_invalid_pagination(self, client: TestClient, query: str) -> None:
        file_id = upload_kml(client, sample_kml())
        response = client.get(f"/api/files/{file_id}/features/?{query}")
        assert response.status_code == 422
        assert response.json()["error"]["code"] == "VALIDATION_ERROR"


class TestMeasurements:
    def test_kml_measurements(self, client: TestClient) -> None:
        file_id = upload_kml(client, sample_kml())
        body = client.get(f"/api/files/{file_id}/measurements/").json()

        assert body["file_id"] == file_id
        assert body["source_crs"] == "EPSG:4326"
        polygon, line, point = body["items"]

        assert polygon["status"] == "MEASURED"
        area = next(m for m in polygon["measurements"] if m["type"] == "area")
        assert area["unit"] == "m²"
        assert area["method"] == "UTM"
        assert area["measurement_crs"] == "EPSG:32643"
        assert relative_error(area["value"], geodesic_area(Polygon(BLR_POLYGON_COORDS))) < 0.005

        assert line["status"] == "MEASURED"
        (length,) = line["measurements"]
        assert length["type"] == "length"
        assert relative_error(length["value"], geodesic_length(LineString(BLR_LINE_COORDS))) < 0.005

        assert point["status"] == "NOT_APPLICABLE"
        assert point["measurements"] == []

        summary = body["summary"]
        assert summary["feature_count"] == 3
        assert summary["status_counts"] == {
            "MEASURED": 2,
            "NOT_APPLICABLE": 1,
            "UNSUPPORTED_GEOMETRY": 0,
            "INVALID_GEOMETRY": 0,
            "EMPTY_GEOMETRY": 0,
            "UNKNOWN_CRS": 0,
            "FAILED": 0,
        }
        assert summary["total_area_m2"] == area["value"]
        assert summary["total_length_m"] == length["value"]

    @pytest.mark.parametrize(
        ("kml_index", "geometry"),
        [
            (0, Polygon(BLR_POLYGON_COORDS)),
            (1, LineString(BLR_LINE_COORDS)),
            (2, Point(77.595, 12.975)),
        ],
    )
    def test_shapefile_measurements_match_kml(
        self, client: TestClient, tmp_path: Path, kml_index: int, geometry: BaseGeometry
    ) -> None:
        shp = write_shapefile(tmp_path, [geometry])
        response = client.post(
            "/api/files/", files={"file": ("p.zip", zip_shapefile(shp), "application/zip")}
        )
        shp_item = client.get(f"/api/files/{response.json()['id']}/measurements/").json()["items"][
            0
        ]
        kml_item = client.get(
            f"/api/files/{upload_kml(client, sample_kml())}/measurements/"
        ).json()["items"][kml_index]

        # The same geometry yields the same measurements regardless of input format.
        assert shp_item["status"] == kml_item["status"]
        assert shp_item["measurements"] == kml_item["measurements"]

    def test_projected_shapefile_is_measured_in_its_own_crs(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        square = Polygon(
            [(500_000, 1_430_000), (500_100, 1_430_000), (500_100, 1_430_100), (500_000, 1_430_100)]
        )
        shp = write_shapefile(tmp_path, [square], crs="EPSG:32643")
        response = client.post(
            "/api/files/", files={"file": ("utm.zip", zip_shapefile(shp), "application/zip")}
        )
        item = client.get(f"/api/files/{response.json()['id']}/measurements/").json()["items"][0]

        area = next(m for m in item["measurements"] if m["type"] == "area")
        assert area == {
            "type": "area",
            "value": 10_000.0,
            "unit": "m²",
            "method": "SOURCE_PROJECTED",
            "measurement_crs": "EPSG:32643",
        }

    def test_unsupported_and_invalid_features_do_not_fail_the_file(
        self, client: TestClient
    ) -> None:
        content = kml_document(
            placemark(
                "<MultiGeometry><Point><coordinates>1,2</coordinates></Point>"
                "<LineString><coordinates>1,2 3,4</coordinates></LineString></MultiGeometry>"
            ),
            placemark(
                "<Polygon><outerBoundaryIs><LinearRing><coordinates>"
                "0,0 1,1 1,0 0,1 0,0</coordinates></LinearRing></outerBoundaryIs></Polygon>"
            ),
            placemark("<gx:Track xmlns:gx='http://www.google.com/kml/ext/2.2'/>"),
            placemark("<LineString><coordinates>179.9,0 -179.9,0</coordinates></LineString>"),
        )
        body = client.get(f"/api/files/{upload_kml(client, content)}/measurements/").json()

        assert [i["status"] for i in body["items"]] == [
            "UNSUPPORTED_GEOMETRY",
            "INVALID_GEOMETRY",
            "UNSUPPORTED_GEOMETRY",
            "FAILED",
        ]
        assert all(i["message"] for i in body["items"])
        assert body["summary"]["total_area_m2"] == 0.0

    def test_measurement_pagination_keeps_file_level_summary(self, client: TestClient) -> None:
        file_id = upload_kml(client, sample_kml())
        body = client.get(f"/api/files/{file_id}/measurements/?limit=1&offset=1").json()
        assert [i["feature_index"] for i in body["items"]] == [1]
        assert body["summary"]["feature_count"] == 3
        assert body["summary"]["total_area_m2"] > 0  # polygon is on page 0
