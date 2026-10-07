"""Application exception hierarchy.

Every error raised deliberately by the application derives from ``AppError``
and carries an HTTP status, a stable machine-readable ``code`` and a message
that is safe to show to API clients (no paths, no stack traces).
"""

from typing import Any


class AppError(Exception):
    status_code: int = 400
    code: str = "BAD_REQUEST"

    def __init__(self, message: str, *, details: dict[str, Any] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.details = details or {}


class InvalidUploadError(AppError):
    status_code = 400
    code = "INVALID_UPLOAD"


class UnsupportedFileTypeError(AppError):
    status_code = 400
    code = "UNSUPPORTED_FILE_TYPE"


class InvalidArchiveError(AppError):
    status_code = 400
    code = "INVALID_ARCHIVE"


class InvalidCRSError(AppError):
    status_code = 400
    code = "INVALID_CRS"


class FileTooLargeError(AppError):
    status_code = 413
    code = "FILE_TOO_LARGE"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class FileNotProcessedError(AppError):
    status_code = 409
    code = "FILE_NOT_PROCESSED"


class ProcessingError(AppError):
    """The upload was accepted but its content could not be processed."""

    status_code = 422
    code = "PROCESSING_FAILED"
