import io
import stat
import zipfile
from pathlib import Path

import pytest

from app.core.exceptions import InvalidArchiveError
from app.utils.zip_utils import (
    ArchiveLimits,
    extract_shapefile,
    inspect_shapefile_archive,
    is_unsafe_member_name,
)
from tests.factories import zip_bytes

LIMITS = ArchiveLimits(
    max_members=20, max_extracted_bytes=10 * 1024 * 1024, max_compression_ratio=100
)
COMPONENTS = {"roads.shp": b"shp", "roads.shx": b"shx", "roads.dbf": b"dbf", "roads.prj": b"prj"}


def archive(tmp_path: Path, payload: bytes) -> Path:
    path = tmp_path / "upload.zip"
    path.write_bytes(payload)
    return path


@pytest.mark.parametrize(
    "name",
    [
        "../evil.shp",
        "data/../../evil.shp",
        "..\\..\\evil.shp",
        "/etc/passwd",
        "\\\\server\\share\\x.shp",
        "C:/Windows/evil.shp",
        "c:evil.shp",
    ],
)
def test_unsafe_member_names_detected(name: str) -> None:
    assert is_unsafe_member_name(name)


@pytest.mark.parametrize("name", ["roads.shp", "data/roads.shp", "a..b/roads.shp", "dir/x..shp"])
def test_safe_member_names_allowed(name: str) -> None:
    assert not is_unsafe_member_name(name)


def test_locates_complete_shapefile(tmp_path: Path) -> None:
    result = inspect_shapefile_archive(archive(tmp_path, zip_bytes(COMPONENTS)), LIMITS)
    assert result.layer_name == "roads"
    assert set(result.components) == {".shp", ".shx", ".dbf", ".prj"}


def test_extension_matching_is_case_insensitive(tmp_path: Path) -> None:
    upper = {name.upper(): data for name, data in COMPONENTS.items()}
    result = inspect_shapefile_archive(archive(tmp_path, zip_bytes(upper)), LIMITS)
    assert ".shp" in result.components


def test_ignores_macos_metadata_and_unrelated_files(tmp_path: Path) -> None:
    entries = {**COMPONENTS, "__MACOSX/._roads.shp": b"x", ".DS_Store": b"x", "README.txt": b"x"}
    result = inspect_shapefile_archive(archive(tmp_path, zip_bytes(entries)), LIMITS)
    assert result.components[".shp"].filename == "roads.shp"


@pytest.mark.parametrize("missing", [".shx", ".dbf"])
def test_missing_required_component(tmp_path: Path, missing: str) -> None:
    entries = {k: v for k, v in COMPONENTS.items() if not k.endswith(missing)}
    with pytest.raises(InvalidArchiveError, match=f"missing required component.*{missing}"):
        inspect_shapefile_archive(archive(tmp_path, zip_bytes(entries)), LIMITS)


def test_components_must_share_a_stem(tmp_path: Path) -> None:
    entries = {"roads.shp": b"", "roads.shx": b"", "other.dbf": b""}
    with pytest.raises(InvalidArchiveError, match="missing"):
        inspect_shapefile_archive(archive(tmp_path, zip_bytes(entries)), LIMITS)


def test_no_shapefile(tmp_path: Path) -> None:
    with pytest.raises(InvalidArchiveError, match=r"does not contain a \.shp"):
        inspect_shapefile_archive(archive(tmp_path, zip_bytes({"notes.txt": b"x"})), LIMITS)


def test_multiple_shapefiles_rejected(tmp_path: Path) -> None:
    second = {k.replace("roads", "parcels"): v for k, v in COMPONENTS.items()}
    with pytest.raises(InvalidArchiveError, match="2 shapefiles"):
        inspect_shapefile_archive(archive(tmp_path, zip_bytes({**COMPONENTS, **second})), LIMITS)


def test_not_a_zip(tmp_path: Path) -> None:
    with pytest.raises(InvalidArchiveError, match="not a valid ZIP"):
        inspect_shapefile_archive(archive(tmp_path, b"PK\x03\x04 truncated garbage"), LIMITS)


def test_path_traversal_rejected(tmp_path: Path) -> None:
    entries = {**COMPONENTS, "../../outside.txt": b"pwned"}
    with pytest.raises(InvalidArchiveError, match="unsafe path"):
        inspect_shapefile_archive(archive(tmp_path, zip_bytes(entries)), LIMITS)


def test_symlink_rejected(tmp_path: Path) -> None:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as zf:
        for name, data in COMPONENTS.items():
            zf.writestr(name, data)
        link = zipfile.ZipInfo("link.shp")
        link.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(link, "/etc/passwd")
    with pytest.raises(InvalidArchiveError, match="symbolic links"):
        inspect_shapefile_archive(archive(tmp_path, buffer.getvalue()), LIMITS)


def test_too_many_members(tmp_path: Path) -> None:
    entries = {**COMPONENTS, **{f"junk{i}.txt": b"" for i in range(30)}}
    with pytest.raises(InvalidArchiveError, match="entries"):
        inspect_shapefile_archive(archive(tmp_path, zip_bytes(entries)), LIMITS)


def test_zip_bomb_compression_ratio_rejected(tmp_path: Path) -> None:
    entries = {**COMPONENTS, "roads.dbf": b"\x00" * (5 * 1024 * 1024)}
    with pytest.raises(InvalidArchiveError, match="compression ratio"):
        inspect_shapefile_archive(archive(tmp_path, zip_bytes(entries)), LIMITS)


def test_total_uncompressed_size_limit(tmp_path: Path) -> None:
    small = ArchiveLimits(max_members=20, max_extracted_bytes=10, max_compression_ratio=100)
    with pytest.raises(InvalidArchiveError, match="uncompressed size"):
        inspect_shapefile_archive(archive(tmp_path, zip_bytes(COMPONENTS)), small)


def test_extraction_uses_fixed_names_inside_destination(tmp_path: Path) -> None:
    entries = {f"deep/nested/{k}": v for k, v in COMPONENTS.items()}
    destination = tmp_path / "out"
    destination.mkdir()

    shp = extract_shapefile(archive(tmp_path, zip_bytes(entries)), destination, LIMITS)

    assert shp == destination / "layer.shp"
    assert sorted(p.name for p in destination.iterdir()) == [
        "layer.dbf",
        "layer.prj",
        "layer.shp",
        "layer.shx",
    ]
    assert (destination / "layer.dbf").read_bytes() == b"dbf"
