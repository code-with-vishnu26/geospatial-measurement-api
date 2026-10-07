"""Helpers for handling untrusted uploaded files."""

import re
import unicodedata
from pathlib import Path
from typing import BinaryIO

from app.core.enums import FileType
from app.core.exceptions import FileTooLargeError, InvalidUploadError, UnsupportedFileTypeError

CHUNK_SIZE = 1024 * 1024
ZIP_SIGNATURES = (b"PK\x03\x04", b"PK\x05\x06")
UTF16_BOMS = (b"\xff\xfe", b"\xfe\xff")
_EXTENSIONS = {".kml": FileType.KML, ".zip": FileType.SHAPEFILE}
_UNSAFE_CHARS = re.compile(r"[\x00-\x1f\x7f<>:\"|?*]")


def sanitize_filename(filename: str | None, max_length: int = 255) -> str:
    """Return a display-safe name. It is never used to build filesystem paths."""
    name = re.split(r"[\\/]", filename or "")[-1]
    name = unicodedata.normalize("NFC", name)
    name = _UNSAFE_CHARS.sub("_", name).strip(" .")
    return name[:max_length] or "upload"


def detect_file_type(filename: str) -> FileType:
    suffix = Path(filename).suffix.lower()
    try:
        return _EXTENSIONS[suffix]
    except KeyError:
        supported = ", ".join(sorted(_EXTENSIONS))
        raise UnsupportedFileTypeError(
            f"Unsupported file extension '{suffix or '(none)'}'. Supported: {supported}."
        ) from None


def verify_signature(path: Path, file_type: FileType) -> None:
    """Check that the file content matches its extension (magic bytes)."""
    with path.open("rb") as handle:
        head = handle.read(1024)

    if file_type is FileType.SHAPEFILE:
        if not head.startswith(ZIP_SIGNATURES):
            raise InvalidUploadError("File has a .zip extension but is not a ZIP archive.")
        return

    if head.startswith(UTF16_BOMS):
        return  # UTF-16 XML; well-formedness is verified by the KML parser
    text = head.removeprefix(b"\xef\xbb\xbf").lstrip()
    if not text.startswith(b"<"):
        raise InvalidUploadError("File has a .kml extension but is not an XML document.")


def save_stream(source: BinaryIO, destination: Path, max_bytes: int) -> int:
    """Copy ``source`` to ``destination`` in chunks, enforcing ``max_bytes``.

    Returns the number of bytes written. The partial file is removed on failure.
    """
    written = 0
    try:
        with destination.open("wb") as target:
            while chunk := source.read(CHUNK_SIZE):
                written += len(chunk)
                if written > max_bytes:
                    raise FileTooLargeError(
                        f"File exceeds the maximum upload size of {max_bytes // (1024 * 1024)} MB."
                    )
                target.write(chunk)
    except BaseException:
        destination.unlink(missing_ok=True)
        raise

    if written == 0:
        destination.unlink(missing_ok=True)
        raise InvalidUploadError("Uploaded file is empty.")
    return written
