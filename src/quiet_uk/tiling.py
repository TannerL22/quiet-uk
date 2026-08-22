from __future__ import annotations

import math
import tempfile
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import rasterio
from rasterio.errors import NotGeoreferencedWarning
from rasterio.transform import Affine

from .acoustics import (
    aggregate_energy_bounds,
    combine_censored_sources,
    db_to_energy,
)
from .land_mask import read_tile_land_mask
from .raster import align_array_to_grid, grids_match, read_single_band_db
from .wcs import get_coverage


SOURCES = ("road", "rail", "airport")
TILE_BANDS = (
    "combined_reported_lower_db",
    "road_rail_upper_db",
    "airport_reported_lower_db",
    "airport_reported_fraction",
)


@dataclass(frozen=True)
class Tile:
    """A core tile whose bounds and output cells are 100 m-grid aligned."""

    tile_id: str
    row: int
    col: int
    bbox: tuple[float, float, float, float]
    source_resolution_m: int
    output_resolution_m: int

    @property
    def width_m(self) -> int:
        return int(round(self.bbox[2] - self.bbox[0]))

    @property
    def height_m(self) -> int:
        return int(round(self.bbox[3] - self.bbox[1]))

    @property
    def source_shape(self) -> tuple[int, int]:
        return (
            self.height_m // self.source_resolution_m,
            self.width_m // self.source_resolution_m,
        )

    @property
    def output_shape(self) -> tuple[int, int]:
        return (
            self.height_m // self.output_resolution_m,
            self.width_m // self.output_resolution_m,
        )

    def to_dict(self) -> dict:
        return {
            "tile_id": self.tile_id,
            "row": self.row,
            "col": self.col,
            "bbox_epsg27700": list(self.bbox),
            "source_resolution_m": self.source_resolution_m,
            "output_resolution_m": self.output_resolution_m,
            "source_shape": list(self.source_shape),
            "output_shape": list(self.output_shape),
        }


def snap_extent_to_grid(extent, resolution_m: int) -> tuple[int, int, int, int]:
    """Expand an extent outward to exact integer grid boundaries."""
    if resolution_m <= 0:
        raise ValueError("resolution_m must be positive")
    minx, miny, maxx, maxy = extent
    if not (maxx > minx and maxy > miny):
        raise ValueError("extent must have positive width and height")
    return (
        math.floor(minx / resolution_m) * resolution_m,
        math.floor(miny / resolution_m) * resolution_m,
        math.ceil(maxx / resolution_m) * resolution_m,
        math.ceil(maxy / resolution_m) * resolution_m,
    )


def make_tiles(extent, tile_size_m: int = 10_000,
               source_resolution_m: int = 10,
               output_resolution_m: int = 100) -> list[Tile]:
    """Create deterministic north-to-south, west-to-east core tiles."""
    if tile_size_m <= 0 or tile_size_m % output_resolution_m:
        raise ValueError("tile_size_m must be a positive multiple of output_resolution_m")
    if output_resolution_m % source_resolution_m:
        raise ValueError("output_resolution_m must be a multiple of source_resolution_m")

    minx, miny, maxx, maxy = snap_extent_to_grid(extent, output_resolution_m)
    nx = math.ceil((maxx - minx) / tile_size_m)
    ny = math.ceil((maxy - miny) / tile_size_m)
    tiles: list[Tile] = []
    for row in range(ny):
        top = maxy - row * tile_size_m
        bottom = max(miny, top - tile_size_m)
        for col in range(nx):
            left = minx + col * tile_size_m
            right = min(maxx, left + tile_size_m)
            bbox = (left, bottom, right, top)
            width_m = right - left
            height_m = top - bottom
            if width_m % output_resolution_m or height_m % output_resolution_m:
                raise ValueError(f"Tile edge is not {output_resolution_m} m aligned: {bbox}")
            tiles.append(Tile(
                tile_id=f"r{row:04d}c{col:04d}",
                row=row,
                col=col,
                bbox=tuple(float(v) for v in bbox),
                source_resolution_m=source_resolution_m,
                output_resolution_m=output_resolution_m,
            ))
    return tiles


def tile_grid(tile: Tile, crs: str) -> dict:
    """Return the exact target 10 m grid for a tile core."""
    height, width = tile.source_shape
    transform = Affine(
        tile.source_resolution_m, 0.0, tile.bbox[0],
        0.0, -tile.source_resolution_m, tile.bbox[3],
    )
    return {
        "shape": (height, width),
        "crs": crs,
        "transform": tuple(transform),
        "bounds": tuple(tile.bbox),
    }


def _dataset_grid(dataset) -> dict:
    return {
        "shape": (dataset.height, dataset.width),
        "crs": str(dataset.crs),
        "transform": tuple(dataset.transform),
        "bounds": tuple(dataset.bounds),
    }


def _known_energy(array: np.ndarray) -> np.ndarray:
    energy = np.zeros(array.shape, dtype="float64")
    mask = np.isfinite(array)
    energy[mask] = db_to_energy(array[mask])
    return energy


def _block_mean(array: np.ndarray, factor: int) -> np.ndarray:
    h = (array.shape[0] // factor) * factor
    w = (array.shape[1] // factor) * factor
    blocks = array[:h, :w].reshape(h // factor, factor, w // factor, factor)
    return blocks.mean(axis=(1, 3))


def _source_config(config: dict, source: str) -> tuple[str, str, str, str | None]:
    url = config.get("wcs", {}).get(source)
    coverage_id = config.get("coverage_ids", {}).get(source)
    version = config.get("wcs_versions", {}).get(source, "1.0.0")
    format_ = config.get("wcs_formats", {}).get(source)
    if not url or not coverage_id:
        raise ValueError(f"Missing WCS URL or coverage ID for {source}")
    return url, coverage_id, version, format_


def _bbox_intersection(a, b):
    minx = max(float(a[0]), float(b[0]))
    miny = max(float(a[1]), float(b[1]))
    maxx = min(float(a[2]), float(b[2]))
    maxy = min(float(a[3]), float(b[3]))
    return (minx, miny, maxx, maxy) if maxx > minx and maxy > miny else None


def _load_source_for_tile(source: str, tile: Tile, config: dict,
                          target_grid: dict, temp_dir: Path):
    url, coverage_id, version, format_ = _source_config(config, source)
    coverage_bounds = config.get("coverage_bounds_epsg27700", {}).get(source)
    if coverage_bounds is not None and _bbox_intersection(tile.bbox, coverage_bounds) is None:
        return np.full(tile.source_shape, np.nan, dtype="float64"), {
            "skipped_outside_declared_coverage": True,
            "declared_coverage_bounds": list(coverage_bounds),
            "valid_cells_after_alignment": 0,
        }
    width = tile.source_shape[1]
    height = tile.source_shape[0]
    data = get_coverage(
        url,
        coverage_id,
        tile.bbox,
        width,
        height,
        crs=config.get("crs", "EPSG:27700"),
        version=version,
        format_=format_,
        timeout=int(config.get("wcs_request_timeout_s", 180)),
    )
    raw_path = temp_dir / f"{source}.tif"
    raw_path.write_bytes(data)
    with rasterio.open(raw_path) as dataset:
        array, read_diagnostics = read_single_band_db(dataset)
        raw_grid = _dataset_grid(dataset)
    if grids_match(raw_grid, target_grid):
        aligned = array
        alignment = {"performed": False, "method": None}
    else:
        aligned = align_array_to_grid(array, raw_grid, target_grid)
        alignment = {
            "performed": True,
            "method": "nearest-neighbour reproject to tile reference grid",
            "source_grid": raw_grid,
            "target_grid": target_grid,
        }
    return aligned, {
        "raw_grid": raw_grid,
        "alignment": alignment,
        "read_diagnostics": read_diagnostics,
        "valid_cells_after_alignment": int(np.isfinite(aligned).sum()),
    }


def _write_float_bands(path: Path, arrays: list[np.ndarray], names: list[str],
                       tile: Tile, crs: str):
    path.parent.mkdir(parents=True, exist_ok=True)
    height, width = tile.output_shape
    transform = Affine(
        tile.output_resolution_m, 0.0, tile.bbox[0],
        0.0, -tile.output_resolution_m, tile.bbox[3],
    )
    profile = {
        "driver": "GTiff",
        "height": height,
        "width": width,
        "count": len(arrays),
        "dtype": "float32",
        "crs": crs,
        "transform": transform,
        "nodata": -9999.0,
        "compress": "deflate",
    }
    with rasterio.open(path, "w", **profile) as dst:
        for band, (name, array) in enumerate(zip(names, arrays), start=1):
            values = np.asarray(array, dtype="float32")
            dst.write(np.where(np.isfinite(values), values, -9999.0), band)
            dst.set_band_description(band, name)


def validate_tile_output(path: str | Path, tile: Tile, config: dict,
                         expected_bands: list[str] | None = None) -> dict:
    """Validate a completed 100 m tile before it can become resumable state."""
    path = Path(path)
    if not path.exists() or path.stat().st_size == 0:
        raise ValueError(f"Tile output is missing or empty: {path}")
    expected_bands = expected_bands or list(TILE_BANDS)
    with rasterio.open(path) as dataset:
        if dataset.count != len(expected_bands):
            raise ValueError(f"Expected {len(expected_bands)} bands, got {dataset.count}")
        if list(dataset.descriptions) != expected_bands:
            raise ValueError("Tile band descriptions do not match the configured architecture")
        if dataset.shape != tile.output_shape:
            raise ValueError(f"Tile shape {dataset.shape} does not match {tile.output_shape}")
        if str(dataset.crs) != str(config.get("crs", "EPSG:27700")):
            raise ValueError(f"Tile CRS {dataset.crs} does not match configured CRS")
        expected_transform = Affine(
            tile.output_resolution_m, 0.0, tile.bbox[0],
            0.0, -tile.output_resolution_m, tile.bbox[3],
        )
        if not np.allclose(tuple(dataset.transform), tuple(expected_transform), rtol=0.0, atol=1e-6):
            raise ValueError("Tile transform is not aligned to the exact core bounds")
        arrays = dataset.read().astype("float64")
        nodata = dataset.nodata if dataset.nodata is not None else -9999.0
    land_mask_path = config.get("england_mask_100m_path")
    land = None
    outside_cells = 0
    if land_mask_path:
        land = read_tile_land_mask(land_mask_path, tile.bbox, tile.output_shape)
        outside_cells = int((~land).sum())
        if outside_cells and np.any(arrays[:, ~land] > nodata + 1e-6):
            raise ValueError("Tile contains non-nodata values outside the England land mask")
    return {
        "valid": True,
        "path": str(path),
        "bands": expected_bands,
        "shape": list(tile.output_shape),
        "land_cells": int(land.sum()) if land is not None else None,
        "outside_masked_cells": outside_cells,
    }


def process_tile(tile: Tile, config: dict, output_path: str | Path,
                 temp_root: str | Path | None = None) -> dict:
    """Download, validate, combine and aggregate one tile.

    Raw 10 m GeoTIFFs exist only inside a temporary directory and are removed
    after the validated 100 m tile is written. The output deliberately omits a
    total combined upper bound when the airport threshold is not configured.
    """
    source_resolution = int(config.get("pilot_resolution_m", tile.source_resolution_m))
    output_resolution = int(config.get("output_resolution_m", tile.output_resolution_m))
    if source_resolution != tile.source_resolution_m or output_resolution != tile.output_resolution_m:
        raise ValueError("Tile resolution does not match configuration")
    if output_resolution % source_resolution:
        raise ValueError("Output resolution must be a multiple of source resolution")
    factor = output_resolution // source_resolution
    target_grid = tile_grid(tile, config.get("crs", "EPSG:27700"))
    levels: dict[str, np.ndarray] = {}
    source_info: dict[str, dict] = {}

    temp_parent = Path(temp_root) if temp_root else None
    if temp_parent:
        temp_parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix=f"quiet-uk-{tile.tile_id}-", dir=str(temp_parent) if temp_parent else None
    ) as temp_name:
        temp_dir = Path(temp_name)
        for source in SOURCES:
            levels[source], source_info[source] = _load_source_for_tile(
                source, tile, config, target_grid, temp_dir
            )

        thresholds = config.get("reporting_threshold_db", {})
        road_rail_thresholds = {name: float(thresholds[name]) for name in ("road", "rail")}
        road_rail = combine_censored_sources(
            {name: levels[name] for name in ("road", "rail")}, road_rail_thresholds
        )
        combined_reported_energy = sum((_known_energy(levels[name]) for name in SOURCES))
        combined_reported = aggregate_energy_bounds(
            combined_reported_energy, combined_reported_energy, factor=factor
        )
        road_rail_agg = aggregate_energy_bounds(
            road_rail["lower_energy"], road_rail["upper_energy"], factor=factor
        )
        airport_energy = _known_energy(levels["airport"])
        airport_reported = aggregate_energy_bounds(
            airport_energy, airport_energy, factor=factor
        )
        airport_fraction = _block_mean(np.isfinite(levels["airport"]).astype("float64"), factor)

        arrays = [
            combined_reported["lower_db"],
            road_rail_agg["upper_db"],
            airport_reported["lower_db"],
            airport_fraction,
        ]
        names = list(TILE_BANDS)
        airport_threshold = thresholds.get("airport")
        if airport_threshold is not None:
            all_thresholds = {name: float(thresholds[name]) for name in SOURCES}
            combined = combine_censored_sources(levels, all_thresholds)
            combined_upper = aggregate_energy_bounds(
                combined["lower_energy"], combined["upper_energy"], factor=factor
            )
            arrays.append(combined_upper["upper_db"])
            names.append("combined_upper_db")

        land_mask_path = config.get("england_mask_100m_path")
        land_mask = None
        if land_mask_path:
            land_mask = read_tile_land_mask(land_mask_path, tile.bbox, tile.output_shape)
            arrays = [np.where(land_mask, array, np.nan) for array in arrays]

        output_path = Path(output_path)
        _write_float_bands(
            output_path, arrays, names, tile, config.get("crs", "EPSG:27700")
        )
        validation = validate_tile_output(output_path, tile, config, expected_bands=names)

    result = {
        "tile": tile.to_dict(),
        "output": str(output_path),
        "bands": names,
        "airport_threshold_configured": airport_threshold is not None,
        "source_info": source_info,
        "validation": validation,
        "land_mask_applied": land_mask_path is not None,
        "temporary_10m_discarded": True,
    }
    return result


def _read_tile(path: Path):
    with rasterio.open(path) as dataset:
        arrays = dataset.read().astype("float64")
        profile = dataset.profile.copy()
        descriptions = list(dataset.descriptions)
        bounds = tuple(dataset.bounds)
        transform = tuple(dataset.transform)
        crs = str(dataset.crs)
    return arrays, profile, descriptions, bounds, transform, crs


def mosaic_tiles(tile_paths: list[str | Path], output_path: str | Path,
                 expected_extent=None, tolerance: float = 1e-6) -> dict:
    """Mosaic non-overlapping 100 m tiles and reject gaps/duplicates."""
    if not tile_paths:
        raise ValueError("At least one tile is required")
    records = [_read_tile(Path(path)) for path in tile_paths]
    first_arrays, first_profile, descriptions, first_bounds, first_transform, first_crs = records[0]
    resolution_x = float(first_transform[0])
    resolution_y = abs(float(first_transform[4]))
    if not np.isclose(resolution_x, resolution_y, atol=tolerance):
        raise ValueError("Mosaic tiles must have square pixels")
    if expected_extent is None:
        minx = min(record[3][0] for record in records)
        miny = min(record[3][1] for record in records)
        maxx = max(record[3][2] for record in records)
        maxy = max(record[3][3] for record in records)
    else:
        minx, miny, maxx, maxy = expected_extent
    width = int(round((maxx - minx) / resolution_x))
    height = int(round((maxy - miny) / resolution_y))
    if width <= 0 or height <= 0:
        raise ValueError("Mosaic extent is empty")
    mosaic = np.full((first_arrays.shape[0], height, width), -9999.0, dtype="float64")
    occupied = np.zeros((height, width), dtype=bool)
    overlap_cells = 0

    for arrays, profile, tile_descriptions, bounds, transform, crs in records:
        if crs != first_crs or arrays.shape[0] != first_arrays.shape[0]:
            raise ValueError("Tile CRS or band-count mismatch")
        if tile_descriptions != descriptions:
            raise ValueError("Tile band descriptions differ")
        if not np.isclose(transform[0], resolution_x, atol=tolerance) or not np.isclose(abs(transform[4]), resolution_y, atol=tolerance):
            raise ValueError("Tile resolutions differ")
        expected_transform = (
            resolution_x, 0.0, bounds[0], 0.0, -resolution_y, bounds[3],
            0.0, 0.0, 1.0,
        )
        if not np.allclose(transform, expected_transform, rtol=0.0, atol=tolerance):
            raise ValueError("Tile transform is not aligned to its declared bounds")
        col0 = int(round((bounds[0] - minx) / resolution_x))
        row0 = int(round((maxy - bounds[3]) / resolution_y))
        h, w = arrays.shape[1:]
        if col0 < 0 or row0 < 0 or row0 + h > height or col0 + w > width:
            raise ValueError(f"Tile lies outside mosaic extent: {bounds}")
        region = occupied[row0:row0 + h, col0:col0 + w]
        overlap_cells += int(region.sum())
        if region.any():
            raise ValueError(f"Overlapping or duplicated 100 m cells in tile {bounds}")
        mosaic[:, row0:row0 + h, col0:col0 + w] = arrays
        occupied[row0:row0 + h, col0:col0 + w] = True

    gap_cells = int((~occupied).sum())
    if gap_cells:
        raise ValueError(f"Mosaic contains {gap_cells} uncovered 100 m cells")
    out_profile = first_profile.copy()
    out_profile.update(
        height=height,
        width=width,
        count=mosaic.shape[0],
        transform=Affine(resolution_x, 0.0, minx, 0.0, -resolution_y, maxy),
        nodata=-9999.0,
        dtype="float32",
        compress="deflate",
    )
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(output_path, "w", **out_profile) as dst:
        for band in range(mosaic.shape[0]):
            dst.write(mosaic[band].astype("float32"), band + 1)
            dst.set_band_description(band + 1, descriptions[band])
    return {
        "output": str(output_path),
        "tile_count": len(tile_paths),
        "bounds": [minx, miny, maxx, maxy],
        "shape": [height, width],
        "gap_cells": gap_cells,
        "overlap_cells": overlap_cells,
        "crs": first_crs,
        "transform": list(out_profile["transform"]),
    }


def write_tile_boundary_png(mosaic_path: str | Path, tile_paths: list[str | Path],
                            output_path: str | Path, band: int = 1) -> dict:
    """Write a small RGB visual with tile boundaries over a mosaic band."""
    with rasterio.open(mosaic_path) as dataset:
        array = dataset.read(band, masked=True).filled(np.nan).astype("float64")
        transform = dataset.transform
        bounds = tuple(dataset.bounds)
    valid = np.isfinite(array)
    lo = float(np.nanpercentile(array[valid], 1)) if valid.any() else 0.0
    hi = float(np.nanpercentile(array[valid], 99)) if valid.any() else 1.0
    if hi <= lo:
        hi = lo + 1.0
    scaled = np.clip((np.nan_to_num(array, nan=lo) - lo) / (hi - lo), 0.0, 1.0)
    red = (255.0 * scaled).astype("uint8")
    green = (255.0 * (1.0 - np.abs(scaled - 0.5) * 2.0)).astype("uint8")
    blue = (255.0 * (1.0 - scaled)).astype("uint8")
    red[~valid] = green[~valid] = blue[~valid] = 0

    boundary_pixels = []
    for path in tile_paths:
        with rasterio.open(path) as tile_dataset:
            tb = tuple(tile_dataset.bounds)
        x0 = int(round((tb[0] - bounds[0]) / transform.a))
        x1 = int(round((tb[2] - bounds[0]) / transform.a)) - 1
        y0 = int(round((bounds[3] - tb[3]) / abs(transform.e)))
        y1 = int(round((bounds[3] - tb[1]) / abs(transform.e))) - 1
        for x in (x0, x1):
            if 0 <= x < red.shape[1]:
                red[:, x] = 255
                green[:, x] = 255
                blue[:, x] = 255
        for y in (y0, y1):
            if 0 <= y < red.shape[0]:
                red[y, :] = 255
                green[y, :] = 255
                blue[y, :] = 255
        boundary_pixels.append({"x0": x0, "x1": x1, "y0": y0, "y1": y1})

    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    profile = {"driver": "PNG", "width": red.shape[1], "height": red.shape[0], "count": 3, "dtype": "uint8"}
    with np.errstate(all="ignore"):
        import warnings
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", category=NotGeoreferencedWarning)
            with rasterio.open(output_path, "w", **profile) as dst:
                dst.write(red, 1)
                dst.write(green, 2)
                dst.write(blue, 3)
    return {"output": str(output_path), "boundary_pixels": boundary_pixels}
