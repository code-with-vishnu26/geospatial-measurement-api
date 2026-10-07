"""Safe inspection and extraction of Shapefile ZIP archives.

Archives are treated as hostile:

* member names are never used as filesystem paths; components are written to
  fixed names (``layer.shp``, ``layer.dbf``...) inside a directory we own, so
  zip-slip is impossible by construction. Unsafe names are still rejected
  outright because they signal a malicious or broken archive;
* symlinks and encrypted members are rejected;
* member count, total uncompressed size and compression ratio are bounded
  (zip-bomb protection), and the byte budget is enforced on the *actual*
  decompressed stream, not just the sizes declared in the header;
* only the components needed to read the layer are extracted.
"""

import re
import stat
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core.exceptions import InvalidArchiveError

REQUIRED_COMPONENTS = (".shp", ".shx", ".dbf")
OPTIONAL_COMPONENTS = (".prj", ".cpg")
EXTRACTED_STEM = "layer"
_RATIO_CHECK_MIN_BYTES = 1024 * 1024
_DRIVE_LETTER = re.compile(r"^[A-Za-z]:")
_SIZE_LIMIT_MESSAGE = "Archive uncompressed size exceeds the allowed limit."


@dataclass(frozen=True, slots=True)
class ArchiveLimits:
    max_members: int
    max_extracted_bytes: int
    max_compression_ratio: int


@dataclass(frozen=True, slots=True)
class ShapefileArchive:
    layer_name: str
    components: dict[str, zipfile.ZipInfo]
    """Lower-case extension (e.g. ``.shp``) -> archive member."""


def inspect_shapefile_archive(path: Path, limits: ArchiveLimits) -> ShapefileArchive:
    """Validate the archive and locate exactly one complete Shapefile in it."""
    try:
        with zipfile.ZipFile(path) as archive:
            members = archive.infolist()
    except (zipfile.BadZipFile, OSError) as exc:
        raise InvalidArchiveError("File is not a valid ZIP archive.") from exc

    if len(members) > limits.max_members:
        raise InvalidArchiveError(
            f"Archive has {len(members)} entries; the maximum is {limits.max_members}."
        )
    for member in members:
        _check_member(member, limits)

    total = sum(m.file_size for m in members)
    if total > limits.max_extracted_bytes:
        raise InvalidArchiveError(_SIZE_LIMIT_MESSAGE)

    return _locate_shapefile(members)


def extract_shapefile(path: Path, destination: Path, limits: ArchiveLimits) -> Path:
    """Extract the Shapefile components into ``destination``; return the ``.shp`` path."""
    shapefile = inspect_shapefile_archive(path, limits)
    budget = limits.max_extracted_bytes
    with zipfile.ZipFile(path) as archive:
        for extension, member in shapefile.components.items():
            target = destination / f"{EXTRACTED_STEM}{extension}"
            budget -= _copy_member(archive, member, target, budget)
    return destination / f"{EXTRACTED_STEM}.shp"


def is_unsafe_member_name(name: str) -> bool:
    normalized = name.replace("\\", "/")
    return (
        "\x00" in name
        or normalized.startswith("/")
        or bool(_DRIVE_LETTER.match(normalized))
        or ".." in PurePosixPath(normalized).parts
    )


def _check_member(member: zipfile.ZipInfo, limits: ArchiveLimits) -> None:
    if is_unsafe_member_name(member.filename):
        raise InvalidArchiveError("Archive contains an unsafe path (absolute or '..').")
    if stat.S_ISLNK(member.external_attr >> 16):
        raise InvalidArchiveError("Archive contains symbolic links, which are not allowed.")
    if member.flag_bits & 0x1:
        raise InvalidArchiveError("Encrypted archives are not supported.")
    if (
        member.file_size > _RATIO_CHECK_MIN_BYTES
        and member.file_size > limits.max_compression_ratio * max(member.compress_size, 1)
    ):
        raise InvalidArchiveError("Archive compression ratio is suspiciously high.")


def _is_ignorable(path: PurePosixPath) -> bool:
    return "__MACOSX" in path.parts or path.name.startswith(".")


def _locate_shapefile(members: list[zipfile.ZipInfo]) -> ShapefileArchive:
    groups: dict[tuple[str, str], dict[str, zipfile.ZipInfo]] = {}
    for member in members:
        member_path = PurePosixPath(member.filename.replace("\\", "/"))
        if member.is_dir() or _is_ignorable(member_path):
            continue
        extension = member_path.suffix.lower()
        if extension in REQUIRED_COMPONENTS + OPTIONAL_COMPONENTS:
            key = (str(member_path.parent).lower(), member_path.stem.lower())
            groups.setdefault(key, {})[extension] = member

    layers = [(key, parts) for key, parts in groups.items() if ".shp" in parts]
    if not layers:
        raise InvalidArchiveError("Archive does not contain a .shp file.")
    if len(layers) > 1:
        names = ", ".join(sorted(PurePosixPath(p[".shp"].filename).name for _, p in layers))
        raise InvalidArchiveError(
            f"Archive contains {len(layers)} shapefiles ({names}); "
            "upload one shapefile per archive."
        )

    (_, stem), components = layers[0]
    missing = [ext for ext in REQUIRED_COMPONENTS if ext not in components]
    if missing:
        raise InvalidArchiveError(
            f"Shapefile '{stem}' is missing required component(s): {', '.join(missing)}."
        )
    return ShapefileArchive(layer_name=stem, components=components)


def _copy_member(
    archive: zipfile.ZipFile, member: zipfile.ZipInfo, target: Path, budget: int
) -> int:
    written = 0
    try:
        with archive.open(member) as source, target.open("wb") as sink:
            while chunk := source.read(1024 * 1024):
                written += len(chunk)
                if written > budget or written > member.file_size:
                    raise InvalidArchiveError(_SIZE_LIMIT_MESSAGE)
                sink.write(chunk)
    except (zipfile.BadZipFile, zipfile.LargeZipFile, zlib.error, OSError, EOFError) as exc:
        raise InvalidArchiveError("Archive is corrupt and could not be extracted.") from exc
    return written
