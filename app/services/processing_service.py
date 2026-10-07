"""Format-agnostic processing pipeline: parse -> resolve CRS -> measure.

This service has no knowledge of HTTP or the database. It takes a stored
file and returns a result, which is what would run inside a background
worker if processing became asynchronous.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pyproj import CRS

from app.core.enums import FileType
from app.processors.base import GeospatialProcessor, ParsedDataset, ParsedFeature
from app.services.crs_service import crs_identifier
from app.services.measurement_service import FeatureMeasurement, MeasurementService


@dataclass(frozen=True, slots=True)
class ProcessedFeature:
    parsed: ParsedFeature
    measurement: FeatureMeasurement


@dataclass(frozen=True, slots=True)
class ProcessingResult:
    crs: CRS | None
    features: list[ProcessedFeature]
    warnings: list[str]


class ProcessingService:
    def __init__(
        self,
        processors: Mapping[FileType, GeospatialProcessor],
        measurement_service: MeasurementService,
    ) -> None:
        self.processors = processors
        self.measurement_service = measurement_service

    def process(
        self, path: Path, file_type: FileType, assumed_crs: CRS | None = None
    ) -> ProcessingResult:
        dataset = self.processors[file_type].parse(path)
        crs, warnings = _resolve_crs(dataset, assumed_crs)
        features = [
            ProcessedFeature(feature, self.measurement_service.measure(feature, crs))
            for feature in dataset.features
        ]
        return ProcessingResult(crs=crs, features=features, warnings=warnings)


def _resolve_crs(dataset: ParsedDataset, assumed_crs: CRS | None) -> tuple[CRS | None, list[str]]:
    """The CRS declared by the file always wins; ``assumed_crs`` only fills a gap."""
    if assumed_crs is None:
        return dataset.crs, dataset.warnings

    assumed = crs_identifier(assumed_crs)
    if dataset.crs is None:
        return assumed_crs, [f"The file declares no CRS; using assumed_crs {assumed}."]
    if not dataset.crs.equals(assumed_crs, ignore_axis_order=True):
        declared = crs_identifier(dataset.crs)
        return dataset.crs, [
            *dataset.warnings,
            f"assumed_crs {assumed} was ignored because the file declares {declared}.",
        ]
    return dataset.crs, dataset.warnings
