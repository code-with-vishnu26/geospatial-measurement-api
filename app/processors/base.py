"""Format-independent feature model produced by every processor.

Processors translate a specific file format into ``ParsedDataset``; everything
downstream (measurement, persistence, API) only ever sees this model, so
adding a new format (GeoJSON, KMZ, GeoPackage) means adding one processor.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar

from pyproj import CRS
from shapely.geometry.base import BaseGeometry

from app.core.enums import FileType
from app.core.exceptions import ProcessingError


@dataclass(slots=True)
class ParsedFeature:
    index: int
    geometry: BaseGeometry | None
    geometry_type: str | None
    properties: dict[str, Any]
    source_id: str | None = None
    parse_error: str | None = None
    """Set when the feature's geometry exists in the file but could not be decoded."""


@dataclass(slots=True)
class ParsedDataset:
    crs: CRS | None
    features: list[ParsedFeature]
    warnings: list[str] = field(default_factory=list)


class GeospatialProcessor(ABC):
    file_type: ClassVar[FileType]

    def __init__(self, max_features: int) -> None:
        self.max_features = max_features

    @abstractmethod
    def parse(self, path: Path) -> ParsedDataset:
        """Read ``path`` and return its features. Raises ``ProcessingError``."""

    def _check_feature_limit(self, count: int) -> None:
        if count > self.max_features:
            raise ProcessingError(
                f"File contains more than the maximum of {self.max_features} features."
            )
