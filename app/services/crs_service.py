"""CRS detection and selection of a measurement-appropriate projection.

Strategy, evaluated per feature (see README "CRS Strategy"):

1. The feature's bounding box is expressed in WGS 84 lon/lat. Geometries that
   span more than 180 degrees of longitude (antimeridian crossing / global
   extent) or have out-of-range geographic coordinates are rejected.
2. If the source CRS is projected and its scale distortion over the
   feature's extent is within ``max_scale_distortion``, measure natively
   (converting linear units to metres). This rejects distorting projections
   such as Web Mercator or Plate Carree instead of trusting "projected".
3. Otherwise use the UTM zone of the feature's bounding-box centre, subject to
   the same distortion test (and UTM's 80S-84N latitude limits). Small and
   medium features virtually always land here.
4. Otherwise (very large or polar features) compute area in a Lambert
   Azimuthal Equal-Area projection centred on the feature, which preserves
   area exactly at any scale, and lengths geodesically on the WGS 84
   ellipsoid, since no single projection preserves long distances.
"""

from dataclasses import dataclass
from functools import lru_cache

import numpy as np
import shapely
from pyproj import CRS, Proj, Transformer
from pyproj.exceptions import CRSError
from shapely.geometry.base import BaseGeometry

from app.core.enums import MeasurementMethod
from app.core.exceptions import InvalidCRSError

WGS84 = CRS.from_epsg(4326)
UTM_MIN_LAT, UTM_MAX_LAT = -80.0, 84.0


class MeasurementError(Exception):
    """A feature cannot be measured reliably. The message is safe for clients."""


@dataclass(frozen=True, slots=True)
class MeasurementPlan:
    method: MeasurementMethod
    crs_label: str
    transformer: Transformer | None
    """Source -> measurement CRS. ``None`` when measuring in the source CRS."""
    unit_to_metre: float
    geodesic_lengths: bool
    to_wgs84: Transformer | None
    """Source -> WGS 84, used for geodesic lengths. ``None`` if source is WGS 84."""

    def project(self, geometry: BaseGeometry) -> BaseGeometry:
        projected = shapely.force_2d(geometry)
        if self.transformer is not None:
            projected = transform_geometry(projected, self.transformer)
        return projected


def crs_identifier(crs: CRS) -> str:
    """Short stable identifier: ``EPSG:xxxx`` when one matches, else the CRS name."""
    authority = crs.to_authority(min_confidence=90)
    return f"{authority[0]}:{authority[1]}" if authority else crs.name


def parse_crs(value: str) -> CRS:
    try:
        crs = CRS.from_user_input(value.strip())
    except CRSError as exc:
        raise InvalidCRSError(f"'{value[:100]}' is not a recognised CRS.") from exc
    if not (horizontal_crs(crs).is_geographic or horizontal_crs(crs).is_projected):
        raise InvalidCRSError("Only geographic or projected CRSs are supported.")
    return crs


def horizontal_crs(crs: CRS) -> CRS:
    """Return the horizontal component of compound (e.g. 2D + height) or bound CRSs."""
    if crs.is_compound:
        return horizontal_crs(crs.sub_crs_list[0])
    if crs.is_bound and crs.source_crs is not None:
        return crs.source_crs
    return crs


def transform_geometry(geometry: BaseGeometry, transformer: Transformer) -> BaseGeometry:
    def apply(coords: np.ndarray) -> np.ndarray:
        x, y = transformer.transform(coords[:, 0], coords[:, 1])
        return np.column_stack([x, y])

    return shapely.transform(geometry, apply)


def utm_epsg(lon: float, lat: float) -> int:
    zone = min(int((lon + 180.0) // 6.0) + 1, 60)  # lon=180 belongs to zone 60
    return (32600 if lat >= 0 else 32700) + zone


class CRSService:
    def __init__(self, max_scale_distortion: float) -> None:
        self.max_scale_distortion = max_scale_distortion

    def plan(self, geometry: BaseGeometry, source_crs: CRS) -> MeasurementPlan:
        horizontal = horizontal_crs(source_crs)
        if not (horizontal.is_geographic or horizontal.is_projected):
            raise MeasurementError("The file's CRS is neither geographic nor projected.")

        bounds = self._wgs84_bounds(geometry, horizontal)
        to_wgs84 = None if horizontal == WGS84 else _transformer(horizontal.srs, WGS84.srs)

        if horizontal.is_projected and self._is_low_distortion(horizontal, bounds):
            return MeasurementPlan(
                method=MeasurementMethod.SOURCE_PROJECTED,
                crs_label=crs_identifier(horizontal),
                transformer=None,
                unit_to_metre=horizontal.axis_info[0].unit_conversion_factor,
                geodesic_lengths=False,
                to_wgs84=to_wgs84,
            )

        target, method, label = self._local_projection(bounds)
        return MeasurementPlan(
            method=method,
            crs_label=label,
            transformer=_transformer(horizontal.srs, target.srs),
            unit_to_metre=1.0,
            geodesic_lengths=method is MeasurementMethod.LOCAL_EQUAL_AREA,
            to_wgs84=to_wgs84,
        )

    @staticmethod
    def _wgs84_bounds(geometry: BaseGeometry, horizontal: CRS) -> tuple[float, float, float, float]:
        min_x, min_y, max_x, max_y = geometry.bounds
        in_degrees = horizontal.is_geographic and horizontal.axis_info[0].unit_name == "degree"
        if in_degrees and not _is_lonlat_range(min_x, min_y, max_x, max_y):
            raise _out_of_range_error()
        if horizontal != WGS84:
            transformer = _transformer(horizontal.srs, WGS84.srs)
            min_x, min_y, max_x, max_y = transformer.transform_bounds(
                min_x, min_y, max_x, max_y, densify_pts=21
            )
        if not all(np.isfinite((min_x, min_y, max_x, max_y))):
            raise MeasurementError("Geometry could not be transformed to WGS 84.")
        if horizontal.is_geographic and not _is_lonlat_range(min_x, min_y, max_x, max_y):
            raise _out_of_range_error()  # non-degree units (e.g. grads), checked after transform
        # transform_bounds reports antimeridian-crossing extents as min_x > max_x.
        if min_x > max_x or max_x - min_x > 180:
            raise MeasurementError(
                "Geometry spans more than 180 degrees of longitude (it crosses the "
                "antimeridian or is global); it cannot be measured reliably."
            )
        return min_x, min_y, max_x, max_y

    def _is_low_distortion(self, crs: CRS, bounds: tuple[float, float, float, float]) -> bool:
        min_x, min_y, max_x, max_y = bounds
        lons = np.array([min_x, max_x, min_x, max_x, (min_x + max_x) / 2])
        lats = np.array([min_y, min_y, max_y, max_y, (min_y + max_y) / 2])
        try:
            factors = _proj(crs.srs).get_factors(lons, lats, errcheck=True)
        except Exception:  # noqa: BLE001  outside projection domain -> not usable
            return False
        scales = np.concatenate(
            [factors.areal_scale, factors.meridional_scale, factors.parallel_scale]
        )
        return bool(
            np.all(np.isfinite(scales))
            and np.max(np.abs(scales - 1.0)) <= self.max_scale_distortion
        )

    def _local_projection(
        self, bounds: tuple[float, float, float, float]
    ) -> tuple[CRS, MeasurementMethod, str]:
        min_x, min_y, max_x, max_y = bounds
        centre_lon, centre_lat = (min_x + max_x) / 2, (min_y + max_y) / 2

        epsg = utm_epsg(centre_lon, centre_lat)
        utm = _crs_from_epsg(epsg)
        if min_y >= UTM_MIN_LAT and max_y <= UTM_MAX_LAT and self._is_low_distortion(utm, bounds):
            return utm, MeasurementMethod.UTM, f"EPSG:{epsg}"

        proj4 = (
            f"+proj=laea +lat_0={centre_lat:.6f} +lon_0={centre_lon:.6f} "
            "+datum=WGS84 +units=m +no_defs"
        )
        return CRS.from_proj4(proj4), MeasurementMethod.LOCAL_EQUAL_AREA, proj4


def _is_lonlat_range(min_x: float, min_y: float, max_x: float, max_y: float) -> bool:
    # max_x may be < min_x for antimeridian-crossing extents; that is checked separately.
    return all(-180 <= x <= 180 for x in (min_x, max_x)) and -90 <= min_y <= max_y <= 90


def _out_of_range_error() -> MeasurementError:
    return MeasurementError(
        "Coordinates are outside the valid longitude/latitude range for a "
        "geographic CRS; the declared CRS is probably wrong."
    )


@lru_cache(maxsize=256)
def _transformer(source_srs: str, target_srs: str) -> Transformer:
    return Transformer.from_crs(
        CRS.from_user_input(source_srs), CRS.from_user_input(target_srs), always_xy=True
    )


@lru_cache(maxsize=128)
def _crs_from_epsg(code: int) -> CRS:
    return CRS.from_epsg(code)


@lru_cache(maxsize=64)
def _proj(srs: str) -> Proj:
    return Proj(CRS.from_user_input(srs))
