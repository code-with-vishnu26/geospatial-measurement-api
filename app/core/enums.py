"""Enumerations shared across the persistence, service and API layers."""

from enum import StrEnum


class FileType(StrEnum):
    KML = "KML"
    SHAPEFILE = "SHAPEFILE"


class FileStatus(StrEnum):
    UPLOADED = "UPLOADED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"


class MeasurementStatus(StrEnum):
    MEASURED = "MEASURED"
    NOT_APPLICABLE = "NOT_APPLICABLE"
    UNSUPPORTED_GEOMETRY = "UNSUPPORTED_GEOMETRY"
    INVALID_GEOMETRY = "INVALID_GEOMETRY"
    EMPTY_GEOMETRY = "EMPTY_GEOMETRY"
    UNKNOWN_CRS = "UNKNOWN_CRS"
    FAILED = "FAILED"


class MeasurementType(StrEnum):
    AREA = "area"
    PERIMETER = "perimeter"
    LENGTH = "length"


class MeasurementMethod(StrEnum):
    SOURCE_PROJECTED = "SOURCE_PROJECTED"
    """Measured directly in the file's own projected CRS (distortion within tolerance)."""

    UTM = "UTM"
    """Re-projected into the UTM zone containing the feature's centroid."""

    LOCAL_EQUAL_AREA = "LOCAL_EQUAL_AREA"
    """Area in a Lambert Azimuthal Equal-Area projection centred on the feature."""

    GEODESIC = "GEODESIC"
    """Length computed on the WGS 84 ellipsoid (used where no projection is reliable)."""
