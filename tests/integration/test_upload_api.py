from collections.abc import Iterator
from pathlib import Path

from fastapi.testclient import TestClient
from shapely.geometry import Point, Polygon

from app.core.config import Settings
from app.main import create_app
from tests.conftest import TEST_MAX_UPLOAD_BYTES
from tests.factories import (
    BLR_POLYGON_COORDS,
    kml_document,
    sample_kml,
    write_shapefile,
    zip_bytes,
    zip_shapefile,
)

KML_TYPE = "application/vnd.google-earth.kml+xml"


def upload(client: TestClient, name: str, content: bytes | str, **form: str):
    payload = content.encode() if isinstance(content, str) else content
    return client.post(
        "/api/files/", files={"file": (name, payload, "application/octet-stream")}, data=form
    )


def assert_error(response, status: int, code: str) -> dict:
    assert response.status_code == status, response.text
    body = response.json()
    assert set(body) == {"error"}
    assert body["error"]["code"] == code
    assert body["error"]["message"]
    return body["error"]


def stored_uploads(settings: Settings) -> list[Path]:
    return list(settings.upload_dir.iterdir())


class TestValidUploads:
    def test_kml_upload(self, client: TestClient) -> None:
        response = upload(client, "survey.kml", sample_kml())

        assert response.status_code == 201
        body = response.json()
        assert response.headers["location"] == f"/api/files/{body['id']}/"
        assert body["filename"] == "survey.kml"
        assert body["file_type"] == "KML"
        assert body["status"] == "COMPLETED"
        assert body["feature_count"] == 3
        assert body["crs"] == "EPSG:4326"
        assert body["error_message"] is None
        assert body["links"]["measurements"] == f"/api/files/{body['id']}/measurements/"

    def test_shapefile_zip_upload(self, client: TestClient, tmp_path: Path) -> None:
        # A shapefile layer holds a single geometry type.
        shp = write_shapefile(
            tmp_path,
            [Polygon(BLR_POLYGON_COORDS), Polygon(BLR_POLYGON_COORDS).buffer(-0.001)],
            attributes={"name": ["park", "lake"], "code": [1, 2]},
        )
        response = upload(client, "parcels.zip", zip_shapefile(shp))

        assert response.status_code == 201, response.text
        body = response.json()
        assert body["file_type"] == "SHAPEFILE"
        assert body["status"] == "COMPLETED"
        assert body["feature_count"] == 2
        assert body["crs"] == "EPSG:4326"

    def test_projected_shapefile(self, client: TestClient, tmp_path: Path) -> None:
        shp = write_shapefile(tmp_path, [Point(500_000, 1_430_000)], crs="EPSG:32643")
        body = upload(client, "utm.zip", zip_shapefile(shp)).json()
        assert body["crs"] == "EPSG:32643"
        assert body["crs_name"] == "WGS 84 / UTM zone 43N"

    def test_filename_is_sanitised(self, client: TestClient) -> None:
        body = upload(client, "../../secret/survey.kml", sample_kml()).json()
        assert body["filename"] == "survey.kml"


class TestCrsEdgeCases:
    def test_missing_prj_completes_with_warning_and_unknown_crs(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        shp = write_shapefile(tmp_path, [Polygon(BLR_POLYGON_COORDS)])
        response = upload(client, "noprj.zip", zip_shapefile(shp, exclude=(".prj",)))

        body = response.json()
        assert response.status_code == 201
        assert body["status"] == "COMPLETED"
        assert body["crs"] is None
        assert ".prj" in body["warnings"][0]

        measurements = client.get(f"/api/files/{body['id']}/measurements/").json()
        assert measurements["items"][0]["status"] == "UNKNOWN_CRS"
        assert measurements["items"][0]["measurements"] == []

    def test_missing_prj_with_assumed_crs_is_measured(
        self, client: TestClient, tmp_path: Path
    ) -> None:
        shp = write_shapefile(tmp_path, [Polygon(BLR_POLYGON_COORDS)])
        body = upload(
            client, "noprj.zip", zip_shapefile(shp, exclude=(".prj",)), assumed_crs="EPSG:4326"
        ).json()

        assert body["crs"] == "EPSG:4326"
        measurements = client.get(f"/api/files/{body['id']}/measurements/").json()
        assert measurements["items"][0]["status"] == "MEASURED"

    def test_invalid_prj_fails_processing(self, client: TestClient, tmp_path: Path) -> None:
        shp = write_shapefile(tmp_path, [Point(1, 2)])
        response = upload(client, "badprj.zip", zip_shapefile(shp, prj_text="GEOGCS[garbage"))

        error = assert_error(response, 422, "INVALID_CRS")
        file_id = error["details"]["file_id"]
        record = client.get(f"/api/files/{file_id}/").json()
        assert record["status"] == "FAILED"
        assert "coordinate reference system" in record["error_message"]

    def test_invalid_assumed_crs_is_rejected_before_storage(
        self, client: TestClient, settings: Settings
    ) -> None:
        response = upload(client, "a.kml", sample_kml(), assumed_crs="EPSG:not-a-code")
        assert_error(response, 400, "INVALID_CRS")
        assert stored_uploads(settings) == []


class TestInvalidUploads:
    def test_unsupported_extension(self, client: TestClient, settings: Settings) -> None:
        assert_error(upload(client, "data.geojson", "{}"), 400, "UNSUPPORTED_FILE_TYPE")
        assert stored_uploads(settings) == []

    def test_content_does_not_match_extension(self, client: TestClient, settings: Settings) -> None:
        assert_error(upload(client, "fake.zip", sample_kml()), 400, "INVALID_UPLOAD")
        assert_error(upload(client, "fake.kml", b"MZ\x90\x00binary"), 400, "INVALID_UPLOAD")
        assert stored_uploads(settings) == []

    def test_empty_file(self, client: TestClient) -> None:
        assert_error(upload(client, "empty.kml", b""), 400, "INVALID_UPLOAD")

    def test_missing_file_field(self, client: TestClient) -> None:
        error = assert_error(client.post("/api/files/"), 422, "VALIDATION_ERROR")
        assert error["details"]["errors"][0]["location"] == ["body", "file"]

    def test_malformed_zip(self, client: TestClient, settings: Settings) -> None:
        assert_error(upload(client, "broken.zip", b"PK\x03\x04garbage"), 400, "INVALID_ARCHIVE")
        assert stored_uploads(settings) == []

    def test_zip_missing_shapefile_components(self, client: TestClient, tmp_path: Path) -> None:
        shp = write_shapefile(tmp_path, [Point(1, 2)])
        error = assert_error(
            upload(client, "nodbf.zip", zip_shapefile(shp, exclude=(".dbf",))),
            400,
            "INVALID_ARCHIVE",
        )
        assert ".dbf" in error["message"]

    def test_zip_slip_archive(self, client: TestClient, tmp_path: Path) -> None:
        payload = zip_bytes({"a.shp": b"", "a.shx": b"", "a.dbf": b"", "../../evil.py": b"x"})
        assert_error(upload(client, "evil.zip", payload), 400, "INVALID_ARCHIVE")
        assert not (tmp_path / "evil.py").exists()

    def test_malformed_kml_creates_failed_record(self, client: TestClient) -> None:
        error = assert_error(upload(client, "bad.kml", "<kml><Document>"), 422, "PROCESSING_FAILED")
        record = client.get(f"/api/files/{error['details']['file_id']}/").json()
        assert record["status"] == "FAILED"
        assert record["feature_count"] == 0

    def test_error_messages_do_not_leak_server_paths(
        self, client: TestClient, settings: Settings
    ) -> None:
        error = assert_error(upload(client, "bad.kml", "<kml><Document>"), 422, "PROCESSING_FAILED")
        assert str(settings.data_dir) not in error["message"]
        assert "Traceback" not in error["message"]


class TestUploadSizeLimits:
    def test_declared_content_length_over_limit(self, client: TestClient) -> None:
        response = upload(
            client, "big.kml", b"<" + b"a" * (TEST_MAX_UPLOAD_BYTES + 2 * 1024 * 1024)
        )
        assert_error(response, 413, "FILE_TOO_LARGE")

    def test_streamed_body_over_limit_without_content_length(self, client: TestClient) -> None:
        boundary = "testboundary"

        def chunked_body() -> Iterator[bytes]:
            yield (
                f'--{boundary}\r\nContent-Disposition: form-data; name="file"; '
                f'filename="big.kml"\r\nContent-Type: text/xml\r\n\r\n<'
            ).encode()
            for _ in range(8):
                yield b"a" * (512 * 1024)
            yield f"\r\n--{boundary}--\r\n".encode()

        response = client.post(
            "/api/files/",
            content=chunked_body(),
            headers={"Content-Type": f"multipart/form-data; boundary={boundary}"},
        )
        assert_error(response, 413, "FILE_TOO_LARGE")

    def test_file_just_over_limit_is_rejected_and_not_stored(
        self, client: TestClient, settings: Settings
    ) -> None:
        # Fits inside the multipart allowance, so the endpoint's own check must catch it.
        response = upload(client, "big.kml", b"<" + b"a" * TEST_MAX_UPLOAD_BYTES)
        assert_error(response, 413, "FILE_TOO_LARGE")
        assert stored_uploads(settings) == []


class TestEmptyAndListing:
    def test_empty_kml_document(self, client: TestClient) -> None:
        body = upload(client, "empty.kml", kml_document()).json()
        assert body["status"] == "COMPLETED"
        assert body["feature_count"] == 0
        assert body["warnings"]

    def test_list_files_is_paginated(self, client: TestClient) -> None:
        for _ in range(3):
            upload(client, "a.kml", sample_kml())
        page = client.get("/api/files/?limit=2&offset=0").json()
        assert page["total"] == 3
        assert len(page["items"]) == 2

    def test_health(self, client: TestClient) -> None:
        response = client.get("/health")
        assert response.json() == {"status": "ok"}
        assert "x-request-id" in response.headers


def test_unexpected_processing_crash_returns_generic_500(settings: Settings) -> None:
    app = create_app(settings)

    def explode(*_args: object, **_kwargs: object) -> None:
        raise RuntimeError("secret internal detail at /srv/internal/path")

    app.state.processing_service.process = explode
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/api/files/", files={"file": ("a.kml", sample_kml().encode())})
        error = assert_error(response, 500, "INTERNAL_ERROR")
        assert "secret" not in error["message"]

        page = client.get("/api/files/").json()
        assert page["items"][0]["status"] == "FAILED"
        assert page["items"][0]["error_message"] == "Unexpected error while processing the file."
