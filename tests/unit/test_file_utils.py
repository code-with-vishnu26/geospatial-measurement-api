import io
from pathlib import Path

import pytest

from app.core.enums import FileType
from app.core.exceptions import FileTooLargeError, InvalidUploadError, UnsupportedFileTypeError
from app.utils.file_utils import detect_file_type, sanitize_filename, save_stream, verify_signature


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("survey.kml", "survey.kml"),
        ("../../etc/passwd", "passwd"),
        ("C:\\Users\\me\\parcels.zip", "parcels.zip"),
        ("bad\x00name<>.kml", "bad_name__.kml"),
        ("   ", "upload"),
        (None, "upload"),
        ("..", "upload"),
    ],
)
def test_sanitize_filename(raw: str | None, expected: str) -> None:
    assert sanitize_filename(raw) == expected


def test_sanitize_filename_truncates() -> None:
    assert len(sanitize_filename("a" * 1000 + ".kml")) == 255


@pytest.mark.parametrize(
    ("name", "expected"),
    [("a.kml", FileType.KML), ("A.KML", FileType.KML), ("parcels.ZIP", FileType.SHAPEFILE)],
)
def test_detect_file_type(name: str, expected: FileType) -> None:
    assert detect_file_type(name) is expected


@pytest.mark.parametrize("name", ["a.shp", "a.kmz", "a.geojson", "noextension", "a.kml.exe"])
def test_detect_file_type_rejects_unsupported(name: str) -> None:
    with pytest.raises(UnsupportedFileTypeError):
        detect_file_type(name)


@pytest.mark.parametrize(
    ("content", "file_type"),
    [
        (b"PK\x03\x04rest", FileType.SHAPEFILE),
        (b'<?xml version="1.0"?><kml/>', FileType.KML),
        (b"\xef\xbb\xbf\n  <kml/>", FileType.KML),
        ("<kml/>".encode("utf-16"), FileType.KML),
    ],
)
def test_signature_accepted(tmp_path: Path, content: bytes, file_type: FileType) -> None:
    path = tmp_path / "f"
    path.write_bytes(content)
    verify_signature(path, file_type)


@pytest.mark.parametrize(
    ("content", "file_type"),
    [(b"<kml/>", FileType.SHAPEFILE), (b"PK\x03\x04", FileType.KML), (b"MZ\x90\x00", FileType.KML)],
)
def test_signature_mismatch_rejected(tmp_path: Path, content: bytes, file_type: FileType) -> None:
    path = tmp_path / "f"
    path.write_bytes(content)
    with pytest.raises(InvalidUploadError):
        verify_signature(path, file_type)


def test_save_stream_writes_content(tmp_path: Path) -> None:
    target = tmp_path / "out"
    assert save_stream(io.BytesIO(b"hello"), target, max_bytes=10) == 5
    assert target.read_bytes() == b"hello"


def test_save_stream_enforces_limit_and_cleans_up(tmp_path: Path) -> None:
    target = tmp_path / "out"
    with pytest.raises(FileTooLargeError):
        save_stream(io.BytesIO(b"x" * 11), target, max_bytes=10)
    assert not target.exists()


def test_save_stream_rejects_empty_upload(tmp_path: Path) -> None:
    target = tmp_path / "out"
    with pytest.raises(InvalidUploadError, match="empty"):
        save_stream(io.BytesIO(b""), target, max_bytes=10)
    assert not target.exists()
