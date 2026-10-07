"""Shapefile (.zip) processor.

The archive is validated and extracted into a private temporary directory,
then read with GeoPandas (pyogrio/GDAL engine). The CRS is parsed from the
``.prj`` ourselves with pyproj so that a *missing* CRS and an *invalid* CRS
can be told apart and reported differently; GDAL alone collapses both into
"no CRS". No CRS is ever assumed.
"""

import logging
import tempfile
from pathlib import Path

import geopandas as gpd
import pyogrio
from pyogrio.errors import DataLayerError, DataSourceError
from pyproj import CRS
from pyproj.exceptions import CRSError
from shapely.geometry.base import BaseGeometry

from app.core.enums import FileType
from app.core.exceptions import ProcessingError
from app.processors.base import GeospatialProcessor, ParsedDataset, ParsedFeature
from app.utils.geometry_utils import json_safe
from app.utils.zip_utils import ArchiveLimits, extract_shapefile

logger = logging.getLogger(__name__)

MISSING_PRJ_WARNING = (
    "Shapefile has no .prj file, so its CRS is unknown. Features were extracted but "
    "cannot be measured. Re-upload with the .prj or pass 'assumed_crs'."
)


class InvalidPrjError(ProcessingError):
    code = "INVALID_CRS"


class ShapefileProcessor(GeospatialProcessor):
    file_type = FileType.SHAPEFILE

    def __init__(self, max_features: int, archive_limits: ArchiveLimits) -> None:
        super().__init__(max_features)
        self.archive_limits = archive_limits

    def parse(self, path: Path) -> ParsedDataset:
        with tempfile.TemporaryDirectory(prefix="shp-") as workdir:
            shp_path = extract_shapefile(path, Path(workdir), self.archive_limits)
            crs, warnings = self._read_crs(shp_path.with_suffix(".prj"))
            frame = self._read_frame(shp_path)

        attribute_columns = [c for c in frame.columns if c != frame.geometry.name]
        features = [
            self._to_feature(position, fid, geometry, row)
            for position, (fid, geometry, row) in enumerate(
                zip(
                    frame.index,
                    frame.geometry,
                    frame[attribute_columns].to_dict("records"),
                    strict=True,
                )
            )
        ]
        if not features:
            warnings.append("The shapefile contains no features.")
        return ParsedDataset(crs=crs, features=features, warnings=warnings)

    @staticmethod
    def _read_crs(prj_path: Path) -> tuple[CRS | None, list[str]]:
        if not prj_path.exists():
            return None, [MISSING_PRJ_WARNING]
        text = prj_path.read_text(encoding="utf-8", errors="replace").strip()
        try:
            return CRS.from_user_input(text), []
        except CRSError as exc:
            raise InvalidPrjError(
                "The .prj file does not contain a valid coordinate reference system. "
                "Fix the .prj, or remove it and pass 'assumed_crs'."
            ) from exc

    def _read_frame(self, shp_path: Path) -> gpd.GeoDataFrame:
        try:
            # Check the feature count from the header before loading anything into memory.
            self._check_feature_limit(pyogrio.read_info(shp_path)["features"])
            return gpd.read_file(shp_path, engine="pyogrio", fid_as_index=True)
        except (DataSourceError, DataLayerError, ValueError, OSError) as exc:
            # GDAL messages include internal paths: log them, return a generic message.
            logger.warning("Shapefile read failed", extra={"error": repr(exc)})
            raise ProcessingError(
                "The shapefile could not be read; it may be corrupt or incomplete."
            ) from exc

    @staticmethod
    def _to_feature(
        position: int, fid: object, geometry: BaseGeometry | None, row: dict
    ) -> ParsedFeature:
        geometry = geometry if isinstance(geometry, BaseGeometry) else None
        return ParsedFeature(
            index=position,
            geometry=geometry,
            geometry_type=geometry.geom_type if geometry is not None else None,
            properties={str(k): json_safe(v) for k, v in row.items()},
            source_id=str(fid),
        )
