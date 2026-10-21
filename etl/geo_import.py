"""
Geospatial Data Import Module
Parse GeoJSON, Shapefiles, and KML files into DataFrames with geometry information.
"""

import json
import logging
import re
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
import pandas as pd

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Lazy-imported libraries that may not be installed
# ---------------------------------------------------------------------------

_gpd = None


def _ensure_geopandas():
    """Import geopandas on demand; raise a clear error when missing."""
    global _gpd
    if _gpd is None:
        try:
            import geopandas as _gp

            _gpd = _gp
        except ImportError:
            raise ImportError(
                "geopandas is required for shapefile support. "
                "Install it with: pip install geopandas fiona"
            )
    return _gpd


# ---------------------------------------------------------------------------
# GeoJSON
# ---------------------------------------------------------------------------

def import_geojson(path: Union[str, Path]) -> pd.DataFrame:
    """
    Parse a GeoJSON file into a pandas DataFrame.

    Each feature's properties are flattened into columns.  A ``geometry_type``
    column records the OGC geometry type and a ``geometry_summary`` column
    stores a compact text representation (e.g. coordinates for points, or
    ``POLYGON(<n> coords)``).

    Args:
        path: Path to a ``.geojson`` file (or any JSON file containing a
              GeoJSON FeatureCollection or single Feature).

    Returns:
        DataFrame with one row per feature.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"GeoJSON file not found: {path}")

    with open(path, "r", encoding="utf-8") as fh:
        data = json.load(fh)

    features: List[Dict[str, Any]] = []

    if data.get("type") == "FeatureCollection":
        features = data.get("features", [])
    elif data.get("type") == "Feature":
        features = [data]
    else:
        raise ValueError(
            f"Unsupported GeoJSON top-level type: {data.get('type', '<missing>')}. "
            "Expected 'FeatureCollection' or 'Feature'."
        )

    rows: List[Dict[str, Any]] = []
    for feature in features:
        props = dict(feature.get("properties", {})) if feature.get("properties") else {}
        geom = feature.get("geometry") or {}
        props["geometry_type"] = geom.get("type", "Unknown")
        props["geometry_summary"] = _summarise_geometry(geom)
        # Store raw geometry dict for downstream use (e.g. geopandas)
        props["_raw_geometry"] = geom
        rows.append(props)

    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=["geometry_type", "geometry_summary", "_raw_geometry"])

    logger.info(
        "Imported GeoJSON from %s: %d features, %d columns",
        path.name,
        len(df),
        len(df.columns),
    )
    return df


# ---------------------------------------------------------------------------
# Shapefile (requires geopandas)
# ---------------------------------------------------------------------------

def import_shapefile(path: Union[str, Path]) -> pd.DataFrame:
    """
    Parse an ESRI Shapefile into a pandas DataFrame via geopandas.

    The resulting DataFrame keeps all attribute columns plus a
    ``geometry_type`` column and a ``geometry_summary`` column.  The raw
    Shapely geometry objects are available in the ``_raw_geometry`` column for
    further geospatial processing.

    Args:
        path: Path to any file in the shapefile set (``.shp``, ``.dbf``,
              ``.shx`` etc.).  Typically the ``.shp`` file.

    Returns:
        DataFrame with one row per geometry record.
    """
    gpd = _ensure_geopandas()

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Shapefile not found: {path}")

    gdf = gpd.read_file(path)

    df = gdf.copy()
    df["geometry_type"] = df["geometry"].apply(
        lambda g: g.geom_type if g is not None else "Unknown"
    )
    df["geometry_summary"] = df["geometry"].apply(
        lambda g: _summarise_shapely_geometry(g)
    )
    # Rename geometry column to avoid collision with any attribute column
    if "geometry" in df.columns and "_raw_geometry" not in df.columns:
        df["_raw_geometry"] = df["geometry"]
        # Keep the column as-is; users can still access the shapely objects.

    logger.info(
        "Imported shapefile from %s: %d records, %d columns",
        path.name,
        len(df),
        len(df.columns),
    )
    return df


# ---------------------------------------------------------------------------
# KML
# ---------------------------------------------------------------------------

def import_kml(path: Union[str, Path]) -> pd.DataFrame:
    """
    Parse a KML file into a pandas DataFrame.

    Extracts ``name``, ``description``, placemark coordinates, and geometry
    type.  Works with standard KML without requiring any external libraries
    beyond the Python standard library.

    Args:
        path: Path to a ``.kml`` file.

    Returns:
        DataFrame with one row per Placemark.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"KML file not found: {path}")

    raw_xml = path.read_text(encoding="utf-8")
    rows = _parse_kml_xml(raw_xml)

    df = pd.DataFrame(rows)
    if df.empty:
        df = pd.DataFrame(columns=["name", "description", "coordinates", "geometry_type", "geometry_summary"])

    logger.info(
        "Imported KML from %s: %d placemarks, %d columns",
        path.name,
        len(df),
        len(df.columns),
    )
    return df


# ---------------------------------------------------------------------------
# Auto-detect geo columns
# ---------------------------------------------------------------------------

LATITUDE_NAMES = {
    "lat", "latitude", "y", "lat_dd", "lat_dec", "latitud", "latitudine",
    "breite", "lat_dms",
}
LONGITUDE_NAMES = {
    "lon", "lng", "longitude", "x", "lon_dd", "lon_dec", "longitud",
    "longitudine", "laenge", "lngitude", "lon_dms",
}
REGION_NAMES = {
    "region", "state", "province", "county", "country", "city", "district",
    "territory", "area", "zone", "municipality", "suburb", "postcode",
    "zip", "zipcode", "postal_code", "admin_level",
}


def auto_detect_geo_columns(df: pd.DataFrame) -> Dict[str, Optional[str]]:
    """
    Detect latitude, longitude, and region columns in a DataFrame.

    The detection uses a combination of column-name heuristics and value-range
    validation (latitude must be in -90..90, longitude in -180..180).

    Args:
        df: The DataFrame to inspect.

    Returns:
        Dictionary with keys ``latitude``, ``longitude``, ``region``, and
        ``geometry`` (the name of a geometry column if one exists).  Values
        are column names or ``None`` if nothing was detected.
    """
    result: Dict[str, Optional[str]] = {
        "latitude": None,
        "longitude": None,
        "region": None,
        "geometry": None,
    }

    normalised: Dict[str, str] = {c.lower().strip(): c for c in df.columns}

    # --- Latitude ---
    for alias in LATITUDE_NAMES:
        if alias in normalised:
            col_name = normalised[alias]
            if _is_valid_latitude(df[col_name]):
                result["latitude"] = col_name
                break

    # --- Longitude ---
    for alias in LONGITUDE_NAMES:
        if alias in normalised:
            col_name = normalised[alias]
            if _is_valid_longitude(df[col_name]):
                result["longitude"] = col_name
                break

    # --- Region ---
    for alias in REGION_NAMES:
        if alias in normalised:
            result["region"] = normalised[alias]
            break

    # --- Geometry (shapely objects or dict) ---
    for col in df.columns:
        if col.lower() in ("geometry", "geom", "shape", "geo"):
            sample = df[col].dropna().head(1)
            if len(sample) > 0:
                val = sample.iloc[0]
                if _looks_like_geometry(val):
                    result["geometry"] = col
                    break

    logger.debug("Auto-detected geo columns: %s", result)
    return result


# ===================================================================
# Internal helpers
# ===================================================================

def _summarise_geometry(geom: Dict[str, Any]) -> str:
    """Return a compact text summary of a GeoJSON geometry dict."""
    if not geom:
        return "null"
    gtype = geom.get("type", "Unknown")
    coords = geom.get("coordinates")

    if gtype == "Point":
        if coords and len(coords) >= 2:
            return f"Point({coords[0]:.6f}, {coords[1]:.6f})"
        return "Point(???)"
    if gtype in ("MultiPoint", "LineString"):
        n = len(coords) if coords else 0
        return f"{gtype}({n} points)"
    if gtype == "MultiLineString":
        n = sum(len(seg) for seg in (coords or []))
        return f"MultiLineString({n} points)"
    if gtype == "Polygon":
        n = sum(len(ring) for ring in (coords or []))
        return f"Polygon({n} coords)"
    if gtype == "MultiPolygon":
        n = sum(
            sum(len(ring) for ring in poly)
            for poly in (coords or [])
        )
        return f"MultiPolygon({n} coords)"
    if gtype == "GeometryCollection":
        geoms = geom.get("geometries", [])
        return f"GeometryCollection({len(geoms)} geometries)"

    return gtype


def _summarise_shapely_geometry(geom: Any) -> str:
    """Return a compact text summary of a Shapely geometry object."""
    if geom is None:
        return "null"
    try:
        return str(geom)[:120]
    except Exception:
        return "Unknown"


def _parse_kml_xml(xml_text: str) -> List[Dict[str, Any]]:
    """
    Lightweight KML parser using only the standard library.

    Extracts Placemarks with name, description, and coordinates.
    """
    rows: List[Dict[str, Any]] = []

    # Split on <Placemark> boundaries
    placemark_chunks = re.split(r"<Placemark\b", xml_text, flags=re.IGNORECASE)

    for chunk in placemark_chunks[1:]:  # skip first chunk (before first Placemark)
        name = _extract_tag(chunk, "name")
        description = _extract_tag(chunk, "description")

        # Try to find coordinates
        coords_raw = _extract_tag(chunk, "coordinates")
        coordinates = coords_raw.strip() if coords_raw else ""

        # Determine geometry type
        gtype = "Point"
        if re.search(r"<LineString\b", chunk, re.IGNORECASE):
            gtype = "LineString"
        if re.search(r"<Polygon\b", chunk, re.IGNORECASE):
            gtype = "Polygon"
        if re.search(r"<MultiGeometry\b", chunk, re.IGNORECASE):
            gtype = "MultiGeometry"

        # Summarise coordinates
        coord_count = len(coordinates.split()) if coordinates else 0
        summary = f"{gtype}({coord_count} coords)" if coordinates else gtype

        rows.append({
            "name": name,
            "description": description,
            "coordinates": coordinates,
            "geometry_type": gtype,
            "geometry_summary": summary,
        })

    return rows


def _extract_tag(text: str, tag: str) -> str:
    """Extract text content of an XML tag (first occurrence)."""
    pattern = re.compile(
        rf"<{tag}\b[^>]*>(.*?)</{tag}>",
        re.IGNORECASE | re.DOTALL,
    )
    match = pattern.search(text)
    if match:
        return match.group(1).strip()
    return ""


def _is_valid_latitude(series: pd.Series) -> bool:
    """Check if a Series contains plausible latitude values."""
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return False
    return bool((numeric >= -90).all() and (numeric <= 90).all())


def _is_valid_longitude(series: pd.Series) -> bool:
    """Check if a Series contains plausible longitude values."""
    numeric = pd.to_numeric(series, errors="coerce").dropna()
    if numeric.empty:
        return False
    return bool((numeric >= -180).all() and (numeric <= 180).all())


def _looks_like_geometry(val: Any) -> bool:
    """Heuristic check: does the value look like a geometry object?"""
    if val is None:
        return False
    if isinstance(val, dict):
        return val.get("type") in ("Point", "LineString", "Polygon", "MultiPoint",
                                    "MultiLineString", "MultiPolygon",
                                    "GeometryCollection")
    # Shapely-like objects
    return hasattr(val, "geom_type")
