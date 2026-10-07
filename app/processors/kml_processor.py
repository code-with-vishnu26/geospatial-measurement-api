"""KML processor.

KML is parsed directly with ``defusedxml`` rather than through GDAL's KML
driver because:

* GDAL's built-in KML driver only exposes ``name``/``description`` and drops
  ``ExtendedData`` attributes (the LIBKML driver keeps them but is not shipped
  in the standard wheels), which would silently lose data.
* ``defusedxml`` rejects XML entity-expansion and external-entity attacks
  (billion laughs, XXE), which matters for untrusted uploads.

Per the OGC KML 2.2 specification, coordinates are always WGS 84
longitude/latitude, so the dataset CRS is EPSG:4326 by definition.
"""

import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any
from xml.etree.ElementTree import Element

import defusedxml.ElementTree as SafeET
from defusedxml import DefusedXmlException
from pyproj import CRS
from shapely.errors import ShapelyError
from shapely.geometry import (
    GeometryCollection,
    LinearRing,
    LineString,
    MultiLineString,
    MultiPoint,
    MultiPolygon,
    Point,
    Polygon,
)
from shapely.geometry.base import BaseGeometry

from app.core.enums import FileType
from app.core.exceptions import ProcessingError
from app.processors.base import GeospatialProcessor, ParsedDataset, ParsedFeature

KML_CRS = CRS.from_epsg(4326)

_GEOMETRY_TAGS = {"Point", "LineString", "LinearRing", "Polygon", "MultiGeometry"}
_UNSUPPORTED_GEOMETRY_TAGS = {"Model", "Track", "MultiTrack"}
_CONTAINER_TAGS = {"Document", "Folder"}
_COMMA_WITH_SPACES = re.compile(r"\s*,\s*")


class KMLGeometryError(ValueError):
    pass


def _local(tag: str) -> str:
    """Strip the XML namespace so KML 2.0/2.1/2.2 and gx: documents parse alike."""
    return tag.rsplit("}", 1)[-1]


def _child(element: Element, name: str) -> Element | None:
    return next((c for c in element if _local(c.tag) == name), None)


def _children(element: Element, name: str) -> list[Element]:
    return [c for c in element if _local(c.tag) == name]


def _text(element: Element | None) -> str | None:
    if element is None or element.text is None:
        return None
    return element.text.strip() or None


class KMLProcessor(GeospatialProcessor):
    file_type = FileType.KML

    def parse(self, path: Path) -> ParsedDataset:
        root = self._load(path)
        features: list[ParsedFeature] = []
        for folder_path, placemark in self._iter_placemarks(root, ()):
            self._check_feature_limit(len(features) + 1)
            features.append(self._parse_placemark(len(features), placemark, folder_path))

        warnings = [] if features else ["The KML document contains no Placemark features."]
        return ParsedDataset(crs=KML_CRS, features=features, warnings=warnings)

    @staticmethod
    def _load(path: Path) -> Element:
        try:
            root = SafeET.parse(path).getroot()
        except DefusedXmlException as exc:
            raise ProcessingError(
                "KML contains forbidden XML constructs (DTD entities or external references)."
            ) from exc
        except SafeET.ParseError as exc:
            raise ProcessingError(f"KML is not well-formed XML: {exc}") from exc
        if _local(root.tag) != "kml":
            raise ProcessingError("XML document is not KML (root element must be <kml>).")
        return root

    @staticmethod
    def _iter_placemarks(
        root: Element, folder_path: tuple[str, ...]
    ) -> Iterator[tuple[tuple[str, ...], Element]]:
        """Depth-first, document-ordered walk. Iterative, so hostile nesting depth
        cannot exhaust the interpreter stack."""
        stack: list[tuple[Element, tuple[str, ...]]] = [(root, folder_path)]
        while stack:
            element, path = stack.pop()
            for child in reversed(list(element)):
                name = _local(child.tag)
                if name in _CONTAINER_TAGS:
                    folder_name = _text(_child(child, "name"))
                    stack.append((child, (*path, folder_name) if folder_name else path))
                elif name == "Placemark":
                    stack.append((child, path))
            if _local(element.tag) == "Placemark":
                yield path, element

    def _parse_placemark(
        self, index: int, placemark: Element, folder_path: tuple[str, ...]
    ) -> ParsedFeature:
        properties = self._properties(placemark, folder_path)
        source_id = placemark.get("id")

        geometry_element = next(
            (c for c in placemark if _local(c.tag) in _GEOMETRY_TAGS | _UNSUPPORTED_GEOMETRY_TAGS),
            None,
        )
        if geometry_element is None:
            return ParsedFeature(index, None, None, properties, source_id)

        tag = _local(geometry_element.tag)
        if tag in _UNSUPPORTED_GEOMETRY_TAGS:
            return ParsedFeature(index, None, tag, properties, source_id)

        try:
            geometry = self._geometry(geometry_element)
        except (KMLGeometryError, ValueError, ShapelyError) as exc:
            return ParsedFeature(index, None, tag, properties, source_id, parse_error=str(exc))
        return ParsedFeature(index, geometry, geometry.geom_type, properties, source_id)

    @staticmethod
    def _properties(placemark: Element, folder_path: tuple[str, ...]) -> dict[str, Any]:
        properties: dict[str, Any] = {}
        for field in ("name", "description", "styleUrl"):
            value = _text(_child(placemark, field))
            if value is not None:
                properties[field] = value
        if folder_path:
            properties["folder"] = "/".join(folder_path)

        extended = _child(placemark, "ExtendedData")
        if extended is None:
            return properties

        for key, value in _extended_data(extended):
            # Never let ExtendedData overwrite a core field: keep both.
            properties[f"data.{key}" if key in properties else key] = value
        return properties

    def _geometry(self, element: Element) -> BaseGeometry:
        tag = _local(element.tag)
        if tag == "Point":
            coords = _coordinates(element)
            if len(coords) != 1:
                raise KMLGeometryError("Point must contain exactly one coordinate.")
            return Point(coords[0])
        if tag == "LineString":
            return LineString(_coordinates(element))
        if tag == "LinearRing":
            return LinearRing(_coordinates(element))
        if tag == "Polygon":
            return _polygon(element)
        return self._multi_geometry(element)

    def _multi_geometry(self, element: Element) -> BaseGeometry:
        """Flatten (arbitrarily nested) MultiGeometry iteratively, in document order."""
        parts: list[BaseGeometry] = []
        stack = list(reversed(list(element)))
        while stack:
            child = stack.pop()
            tag = _local(child.tag)
            if tag in _UNSUPPORTED_GEOMETRY_TAGS:
                raise KMLGeometryError(f"MultiGeometry contains unsupported <{tag}>.")
            if tag == "MultiGeometry":
                stack.extend(reversed(list(child)))
            elif tag in _GEOMETRY_TAGS:
                parts.append(self._geometry(child))
        return _combine(parts)


def _extended_data(extended: Element) -> Iterator[tuple[str, str | None]]:
    for data in _children(extended, "Data"):
        if name := data.get("name"):
            yield name, _text(_child(data, "value"))
    for schema_data in _children(extended, "SchemaData"):
        for simple in _children(schema_data, "SimpleData"):
            if name := simple.get("name"):
                yield name, _text(simple)


def _coordinates(element: Element) -> list[tuple[float, ...]]:
    raw = _text(_child(element, "coordinates"))
    if raw is None:
        raise KMLGeometryError(f"<{_local(element.tag)}> has no <coordinates>.")
    coords: list[tuple[float, ...]] = []
    # Tuples are whitespace-separated; tolerate "lon, lat" as Google Earth does.
    for token in _COMMA_WITH_SPACES.sub(",", raw).split():
        parts = token.split(",")
        if len(parts) not in (2, 3):
            raise KMLGeometryError(f"Malformed coordinate tuple '{token[:50]}'.")
        try:
            coords.append(tuple(float(p) for p in parts))
        except ValueError as exc:
            raise KMLGeometryError(f"Non-numeric coordinate '{token[:50]}'.") from exc
    if len({len(c) for c in coords}) > 1:
        # Altitude is optional per tuple (KML default 0); keep dimensions uniform.
        coords = [c if len(c) == 3 else (*c, 0.0) for c in coords]
    return coords


def _polygon(element: Element) -> Polygon:
    def ring(boundary: Element) -> list[tuple[float, ...]]:
        linear_ring = _child(boundary, "LinearRing")
        if linear_ring is None:
            raise KMLGeometryError("Polygon boundary has no <LinearRing>.")
        return _coordinates(linear_ring)

    outer = _child(element, "outerBoundaryIs")
    if outer is None:
        raise KMLGeometryError("Polygon has no <outerBoundaryIs>.")
    holes = [ring(b) for b in _children(element, "innerBoundaryIs")]
    return Polygon(ring(outer), holes)


def _combine(parts: list[BaseGeometry]) -> BaseGeometry:
    if not parts:
        return GeometryCollection()
    types = {p.geom_type for p in parts}
    if types == {"Point"}:
        return MultiPoint(parts)
    if types <= {"LineString", "LinearRing"}:
        return MultiLineString([LineString(p.coords) for p in parts])
    if types == {"Polygon"}:
        return MultiPolygon(parts)
    return GeometryCollection(parts)
