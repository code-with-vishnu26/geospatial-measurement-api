"""Generate the sample files in ``samples/`` used by the README examples.

Usage: python scripts/generate_samples.py
"""

import tempfile
import zipfile
from pathlib import Path

import geopandas as gpd
import shapely
from pyproj import Transformer
from shapely.geometry import LineString, Polygon

SAMPLES = Path(__file__).resolve().parent.parent / "samples"

LALBAGH = Polygon(
    [
        (77.5800, 12.9530),
        (77.5890, 12.9530),
        (77.5905, 12.9480),
        (77.5850, 12.9445),
        (77.5795, 12.9475),
        (77.5800, 12.9530),
    ]
)
CUBBON_PARK = Polygon(
    [
        (77.5900, 12.9790),
        (77.5990, 12.9790),
        (77.5990, 12.9710),
        (77.5900, 12.9710),
        (77.5900, 12.9790),
    ]
)
MG_ROAD = LineString([(77.6010, 12.9750), (77.6070, 12.9745), (77.6130, 12.9740)])

KML = """<?xml version="1.0" encoding="UTF-8"?>
<kml xmlns="http://www.opengis.net/kml/2.2">
  <Document>
    <name>Bengaluru survey</name>
    <Folder>
      <name>Parks</name>
      <Placemark id="lalbagh">
        <name>Lalbagh Botanical Garden</name>
        <ExtendedData>
          <Data name="category"><value>botanical garden</value></Data>
          <Data name="surveyed_by"><value>Team A</value></Data>
        </ExtendedData>
        <Polygon><outerBoundaryIs><LinearRing><coordinates>{lalbagh}</coordinates></LinearRing></outerBoundaryIs></Polygon>
      </Placemark>
      <Placemark id="cubbon">
        <name>Cubbon Park</name>
        <Polygon><outerBoundaryIs><LinearRing><coordinates>{cubbon}</coordinates></LinearRing></outerBoundaryIs></Polygon>
      </Placemark>
    </Folder>
    <Folder>
      <name>Roads</name>
      <Placemark id="mg-road">
        <name>MG Road</name>
        <LineString><coordinates>{mg_road}</coordinates></LineString>
      </Placemark>
    </Folder>
    <Placemark id="survey-marker">
      <name>Survey control point</name>
      <Point><coordinates>77.5946,12.9716,920</coordinates></Point>
    </Placemark>
  </Document>
</kml>
"""


def coords(geometry: Polygon | LineString) -> str:
    ring = geometry.exterior.coords if isinstance(geometry, Polygon) else geometry.coords
    return " ".join(f"{x},{y}" for x, y in ring)


def zip_shapefile(frame: gpd.GeoDataFrame, stem: str, target: Path) -> None:
    with tempfile.TemporaryDirectory() as workdir:
        frame.to_file(Path(workdir) / f"{stem}.shp", engine="pyogrio")
        with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED) as archive:
            for component in sorted(Path(workdir).iterdir()):
                archive.write(component, component.name)


def main() -> None:
    SAMPLES.mkdir(exist_ok=True)
    (SAMPLES / "bengaluru_survey.kml").write_text(
        KML.format(lalbagh=coords(LALBAGH), cubbon=coords(CUBBON_PARK), mg_road=coords(MG_ROAD)),
        encoding="utf-8",
    )

    parks = gpd.GeoDataFrame(
        {"name": ["Lalbagh", "Cubbon Park"], "ward": [153, 110]},
        geometry=[LALBAGH, CUBBON_PARK],
        crs="EPSG:4326",
    )
    zip_shapefile(parks, "parks", SAMPLES / "bengaluru_parks_wgs84.zip")

    to_utm = Transformer.from_crs(4326, 32643, always_xy=True).transform
    zip_shapefile(
        parks.set_geometry(
            shapely.transform(parks.geometry.values, to_utm, interleaved=False), crs="EPSG:32643"
        ),
        "parks_utm",
        SAMPLES / "bengaluru_parks_utm43n.zip",
    )
    print(f"Samples written to {SAMPLES}")


if __name__ == "__main__":
    main()
