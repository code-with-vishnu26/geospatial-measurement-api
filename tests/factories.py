"""Builders for test inputs. Everything is generated on the fly into temporary
directories so tests never depend on checked-in binaries or external services."""

import io
import math
import zipfile
from pathlib import Path

import geopandas as gpd
import shapely
from pyproj import Geod
from shapely.geometry import Polygon
from shapely.geometry.base import BaseGeometry

KML_NS = "http://www.opengis.net/kml/2.2"
GEOD = Geod(ellps="WGS84")

# ~1.1 km x 1.1 km block in Bengaluru (UTM zone 43N) and a road through it.
BLR_POLYGON_COORDS = [
    (77.59, 12.97),
    (77.60, 12.97),
    (77.60, 12.98),
    (77.59, 12.98),
    (77.59, 12.97),
]
BLR_LINE_COORDS = [(77.59, 12.97), (77.60, 12.98), (77.61, 12.98)]


def kml_document(*placemarks: str, namespace: str = KML_NS) -> str:
    ns = f' xmlns="{namespace}"' if namespace else ""
    return (
        f'<?xml version="1.0" encoding="UTF-8"?>\n<kml{ns}><Document><name>Test</name>'
        + "".join(placemarks)
        + "</Document></kml>"
    )


def coords(points: list[tuple[float, ...]]) -> str:
    return " ".join(",".join(str(v) for v in p) for p in points)


def placemark(geometry_xml: str, name: str = "feature", extra: str = "", pid: str = "") -> str:
    id_attr = f' id="{pid}"' if pid else ""
    return f"<Placemark{id_attr}><name>{name}</name>{extra}{geometry_xml}</Placemark>"


def kml_polygon(outer: list[tuple[float, ...]], *holes: list[tuple[float, ...]]) -> str:
    inner = "".join(
        f"<innerBoundaryIs><LinearRing><coordinates>{coords(h)}</coordinates></LinearRing>"
        "</innerBoundaryIs>"
        for h in holes
    )
    return (
        f"<Polygon><outerBoundaryIs><LinearRing><coordinates>{coords(outer)}</coordinates>"
        f"</LinearRing></outerBoundaryIs>{inner}</Polygon>"
    )


def kml_line(points: list[tuple[float, ...]]) -> str:
    return f"<LineString><coordinates>{coords(points)}</coordinates></LineString>"


def kml_point(lon: float, lat: float) -> str:
    return f"<Point><coordinates>{lon},{lat}</coordinates></Point>"


def sample_kml() -> str:
    return kml_document(
        placemark(
            kml_polygon(BLR_POLYGON_COORDS),
            name="Park",
            pid="park-1",
            extra='<ExtendedData><Data name="owner"><value>BBMP</value></Data></ExtendedData>',
        ),
        placemark(kml_line(BLR_LINE_COORDS), name="Road"),
        placemark(kml_point(77.595, 12.975), name="Gate"),
    )


def write_shapefile(
    directory: Path,
    geometries: list[BaseGeometry | None],
    crs: str | None = "EPSG:4326",
    attributes: dict[str, list] | None = None,
    stem: str = "layer",
) -> Path:
    attributes = attributes or {"name": [f"f{i}" for i in range(len(geometries))]}
    frame = gpd.GeoDataFrame(attributes, geometry=geometries, crs=crs)
    path = directory / f"{stem}.shp"
    frame.to_file(path, engine="pyogrio")
    return path


def zip_shapefile(
    shp_path: Path,
    exclude: tuple[str, ...] = (),
    prefix: str = "",
    prj_text: str | None = None,
) -> bytes:
    """Zip a shapefile's components, optionally dropping or overriding some."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for component in sorted(shp_path.parent.glob(f"{shp_path.stem}.*")):
            extension = component.suffix.lower()
            if extension in exclude or (extension == ".prj" and prj_text is not None):
                continue
            archive.write(component, f"{prefix}{component.name}")
        if prj_text is not None:
            archive.writestr(f"{prefix}{shp_path.stem}.prj", prj_text)
    return buffer.getvalue()


def zip_bytes(entries: dict[str, bytes]) -> bytes:
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, data in entries.items():
            archive.writestr(name, data)
    return buffer.getvalue()


def geodesic_area(geometry: BaseGeometry) -> float:
    # Geod sums *signed* ring areas, so holes must be oriented opposite to shells.
    return abs(GEOD.geometry_area_perimeter(shapely.orient_polygons(geometry))[0])


def geodesic_length(geometry: BaseGeometry) -> float:
    return GEOD.geometry_length(geometry)


def geodesic_circle(lon: float, lat: float, radius_m: float, vertices: int = 720) -> Polygon:
    """Densely sampled circle, so edge interpretation does not affect its area."""
    azimuths = [360.0 * i / vertices for i in range(vertices)]
    lons, lats, _ = GEOD.fwd([lon] * vertices, [lat] * vertices, azimuths, [radius_m] * vertices)
    return Polygon(zip(lons, lats, strict=True))


def relative_error(actual: float, expected: float) -> float:
    return math.fabs(actual - expected) / expected
