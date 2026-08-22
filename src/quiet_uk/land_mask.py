from __future__ import annotations

import json
import math
from pathlib import Path
from urllib.parse import urlencode

import numpy as np
import rasterio
import requests
from rasterio.features import rasterize
from rasterio.transform import Affine
from rasterio.windows import from_bounds


ONS_DATASET_PAGE = (
    "https://www.data.gov.uk/dataset/"
    "8580e329-83c9-4646-bf93-d0411f00c53a/"
    "countries-december-2024-boundaries-uk-bgc1"
)
ONS_FEATURE_SERVICE = (
    "https://services1.arcgis.com/ESMARspQHYMw9BZ9/arcgis/rest/services/"
    "Countries_December_2024_Boundaries_UK_BGC/FeatureServer/0/query"
)
ONS_PRODUCT_DESCRIPTION = (
    "Countries (December 2024) Boundaries UK BGC; Generalised (20m), "
    "clipped to the coastline (Mean High Water mark)"
)
ONS_ATTRIBUTION = "Contains both Ordnance Survey and ONS Intellectual Property Rights."


def _query_url(source_url: str) -> str:
    params = {
        "where": "CTRY24CD='E92000001'",
        "outFields": "CTRY24CD,CTRY24NM",
        "returnGeometry": "true",
        "outSR": "27700",
        "f": "geojson",
    }
    return f"{source_url}?{urlencode(params)}"


def fetch_england_boundary(source_url: str = ONS_FEATURE_SERVICE,
                           timeout: int = 120) -> tuple[dict, str]:
    """Fetch England from the official ONS BGC Feature Service in EPSG:27700."""
    params = {
        "where": "CTRY24CD='E92000001'",
        "outFields": "CTRY24CD,CTRY24NM",
        "returnGeometry": "true",
        "outSR": "27700",
        "f": "geojson",
    }
    response = requests.get(source_url, params=params, timeout=timeout)
    response.raise_for_status()
    payload = response.json()
    if payload.get("type") != "FeatureCollection" or len(payload.get("features", [])) != 1:
        raise ValueError("ONS query did not return exactly one England feature")
    feature = payload["features"][0]
    props = feature.get("properties", {})
    if props.get("CTRY24CD") != "E92000001" or props.get("CTRY24NM") != "England":
        raise ValueError(f"Unexpected ONS country feature: {props}")
    if payload.get("crs", {}).get("properties", {}).get("name") not in {
        "EPSG:27700", "urn:ogc:def:crs:EPSG::27700"
    }:
        raise ValueError("ONS boundary response was not returned in EPSG:27700")
    return payload, _query_url(source_url)


def _iter_xy(value):
    if isinstance(value, (list, tuple)) and len(value) == 2 and all(
        isinstance(item, (int, float)) for item in value
    ):
        yield float(value[0]), float(value[1])
    elif isinstance(value, (list, tuple)):
        for child in value:
            yield from _iter_xy(child)


def _geometry_bounds(geometry: dict) -> tuple[float, float, float, float]:
    points = list(_iter_xy(geometry["coordinates"]))
    if not points:
        raise ValueError("England geometry has no coordinates")
    xs, ys = zip(*points)
    return min(xs), min(ys), max(xs), max(ys)


def _snap_extent(extent, resolution: int) -> tuple[int, int, int, int]:
    minx, miny, maxx, maxy = extent
    return (
        math.floor(minx / resolution) * resolution,
        math.floor(miny / resolution) * resolution,
        math.ceil(maxx / resolution) * resolution,
        math.ceil(maxy / resolution) * resolution,
    )


def _write_mask(path: Path, values: np.ndarray, transform: Affine,
                crs: str, resolution: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    profile = {
        "driver": "GTiff",
        "height": values.shape[0],
        "width": values.shape[1],
        "count": 1,
        "dtype": "uint8",
        "crs": crs,
        "transform": transform,
        "nodata": 0,
        "compress": "deflate",
        "tiled": True,
        "blockxsize": 256,
        "blockysize": 256,
    }
    with rasterio.open(path, "w", **profile) as dataset:
        dataset.write(values.astype("uint8"), 1)
        dataset.set_band_description(1, f"England land mask ({resolution} m)")


def prepare_england_mask(output_dir: str | Path,
                         source_url: str = ONS_FEATURE_SERVICE,
                         target_crs: str = "EPSG:27700",
                         timeout: int = 120) -> dict:
    """Create the 20 m ONS-derived and aligned 100 m England masks."""
    if target_crs != "EPSG:27700":
        raise ValueError("The production mask currently requires EPSG:27700")
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    payload, query_url = fetch_england_boundary(source_url, timeout=timeout)
    feature = payload["features"][0]
    geometry = feature["geometry"]
    raw_bounds = _geometry_bounds(geometry)
    extent = _snap_extent(raw_bounds, 100)
    minx, miny, maxx, maxy = extent
    transform20 = Affine(20, 0, minx, 0, -20, maxy)
    shape20 = ((maxy - miny) // 20, (maxx - minx) // 20)
    mask20 = rasterize(
        [(geometry, 1)],
        out_shape=shape20,
        transform=transform20,
        fill=0,
        dtype="uint8",
        all_touched=False,
    )
    shape100 = ((maxy - miny) // 100, (maxx - minx) // 100)
    mask100 = mask20.reshape(shape100[0], 5, shape100[1], 5).max(axis=(1, 3))
    transform100 = Affine(100, 0, minx, 0, -100, maxy)

    boundary_path = output_dir / "england_boundary_epsg27700.geojson"
    boundary_path.write_text(json.dumps(payload), encoding="utf-8")
    mask20_path = output_dir / "england_20m_mask.tif"
    mask100_path = output_dir / "england_100m_mask.tif"
    _write_mask(mask20_path, mask20, transform20, target_crs, 20)
    _write_mask(mask100_path, mask100, transform100, target_crs, 100)

    metadata = {
        "dataset": ONS_PRODUCT_DESCRIPTION,
        "source_url": query_url,
        "source_service_url": source_url,
        "source_dataset_page": ONS_DATASET_PAGE,
        "source_query": {
            "CTRY24CD": "E92000001",
            "CTRY24NM": "England",
            "outSR": "27700",
        },
        "source_crs": "EPSG:27700",
        "target_crs": target_crs,
        "licence": "No Licence Provided in the data.gov.uk dataset record",
        "attribution": ONS_ATTRIBUTION,
        "raw_geometry_bounds_epsg27700": list(raw_bounds),
        "mask_extent_epsg27700": list(extent),
        "mask_rule": "20 m rasterized boundary; a 100 m cell is England land when any of its 25 20 m subcells is land",
        "mask20_path": str(mask20_path),
        "mask100_path": str(mask100_path),
        "mask20_shape": list(mask20.shape),
        "mask100_shape": list(mask100.shape),
        "england_land_cells_20m": int(mask20.sum()),
        "england_land_cells_100m": int(mask100.sum()),
    }
    metadata_path = output_dir / "metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2), encoding="utf-8")
    return metadata


def read_tile_land_mask(mask_path: str | Path, bbox,
                        output_shape: tuple[int, int]) -> np.ndarray:
    """Read the exact 100 m land mask for a tile, with zero outside England."""
    with rasterio.open(mask_path) as dataset:
        window = from_bounds(*bbox, transform=dataset.transform)
        values = dataset.read(
            1,
            window=window,
            out_shape=(int(output_shape[0]), int(output_shape[1])),
            resampling=rasterio.enums.Resampling.nearest,
            boundless=True,
            fill_value=0,
        )
    return values > 0


def tile_intersects_land(mask_path: str | Path, bbox) -> bool:
    """Return whether a 10 km core tile contains any England 100 m cell."""
    with rasterio.open(mask_path) as dataset:
        window = from_bounds(*bbox, transform=dataset.transform)
        values = dataset.read(1, window=window, boundless=True, fill_value=0)
    return bool(np.any(values > 0))
