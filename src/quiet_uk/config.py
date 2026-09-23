from __future__ import annotations

import copy
import math
import numbers
from collections.abc import Mapping, Sequence
from pathlib import Path


SOURCES = ("road", "rail", "airport")
SUPPORTED_WCS_VERSIONS = ("1.0.0", "2.0.1")
DEFAULT_CRS = "EPSG:27700"
DEFAULT_METRIC = "Lden"
DEFAULT_SOURCE_RESOLUTION_M = 10
DEFAULT_OUTPUT_RESOLUTION_M = 100
DEFAULT_TILE_SIZE_M = 10_000
DEFAULT_WCS_VERSION = "1.0.0"
DEFAULT_WCS_FORMATS = {"1.0.0": "GeoTIFF", "2.0.1": "image/tiff"}
DEFAULT_REPORTING_THRESHOLDS = {"road": 40.0, "rail": 40.0, "airport": None}


def _reject_nonfinite(value, path: str = "config") -> None:
    if isinstance(value, bool) or value is None or isinstance(value, str):
        return
    if isinstance(value, numbers.Real):
        if not math.isfinite(float(value)):
            raise ValueError(f"Configuration contains a non-finite number at {path}")
        return
    if isinstance(value, Mapping):
        for key, child in value.items():
            _reject_nonfinite(child, f"{path}.{key}")
        return
    if isinstance(value, Sequence) and not isinstance(value, (bytes, bytearray)):
        for index, child in enumerate(value):
            _reject_nonfinite(child, f"{path}[{index}]")


def _number(value, name: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, numbers.Real):
        raise ValueError(f"{name} must be a numeric value, not {type(value).__name__}")
    converted = float(value)
    if not math.isfinite(converted):
        raise ValueError(f"{name} must be finite")
    return int(converted) if converted.is_integer() else converted


def _positive_integer(value, name: str) -> int:
    converted = _number(value, name)
    if not isinstance(converted, int) or converted <= 0:
        raise ValueError(f"{name} must be a positive integer")
    return converted


def _mapping(config: Mapping, key: str) -> Mapping:
    value = config.get(key, {})
    if not isinstance(value, Mapping):
        raise ValueError(f"{key} must be an object")
    return value


def _required_string(values: Mapping, key: str, name: str) -> str:
    value = values.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _resolve_existing_file(value, name: str) -> Path:
    if not isinstance(value, (str, Path)) or not str(value).strip():
        raise ValueError(f"{name} must identify an existing file")
    resolved = Path(value).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"{name} does not identify an existing file: {resolved}")
    return resolved


def _normalise_bounds(value, source: str) -> list[int | float]:
    if not isinstance(value, Sequence) or isinstance(value, (str, bytes, bytearray)) or len(value) != 4:
        raise ValueError(
            f"coverage_bounds_epsg27700.{source} must contain exactly four numeric values"
        )
    bounds = [_number(item, f"coverage_bounds_epsg27700.{source}[{index}]") for index, item in enumerate(value)]
    if bounds[2] <= bounds[0] or bounds[3] <= bounds[1]:
        raise ValueError(
            f"coverage_bounds_epsg27700.{source} must have maxx > minx and maxy > miny"
        )
    return bounds


def normalize_production_config(
    config: Mapping,
    *,
    mask_path: str | Path | None = None,
    require_mask: bool = False,
) -> dict:
    """Return a validated effective production configuration copy.

    ``mask_path`` is authoritative when supplied by the runner. Relative paths
    are resolved against the current working directory; callers at a project
    script boundary should resolve project-relative paths before calling this.
    """
    if not isinstance(config, Mapping):
        raise ValueError("Production configuration must be an object")
    _reject_nonfinite(config)
    effective = copy.deepcopy(dict(config))

    crs = config.get("crs", DEFAULT_CRS)
    metric = config.get("metric", DEFAULT_METRIC)
    if not isinstance(crs, str) or not crs.strip():
        raise ValueError("crs must be a non-empty string")
    if not isinstance(metric, str) or not metric.strip():
        raise ValueError("metric must be a non-empty string")
    effective["crs"] = crs.strip()
    effective["metric"] = metric.strip()

    source_resolution = _positive_integer(
        config.get("pilot_resolution_m", DEFAULT_SOURCE_RESOLUTION_M),
        "pilot_resolution_m",
    )
    output_resolution = _positive_integer(
        config.get("output_resolution_m", DEFAULT_OUTPUT_RESOLUTION_M),
        "output_resolution_m",
    )
    tile_size = _positive_integer(
        config.get("tile_size_m", DEFAULT_TILE_SIZE_M),
        "tile_size_m",
    )
    if output_resolution % source_resolution:
        raise ValueError("output_resolution_m must be divisible by pilot_resolution_m")
    if tile_size % output_resolution:
        raise ValueError("tile_size_m must be divisible by output_resolution_m")
    effective["pilot_resolution_m"] = source_resolution
    effective["output_resolution_m"] = output_resolution
    effective["tile_size_m"] = tile_size

    wcs = _mapping(config, "wcs")
    coverage_ids = _mapping(config, "coverage_ids")
    versions = _mapping(config, "wcs_versions")
    formats = config.get("wcs_formats", {})
    if not isinstance(formats, Mapping):
        raise ValueError("wcs_formats must be an object")
    effective_wcs = {}
    effective_coverage_ids = {}
    effective_versions = {}
    effective_formats = {}
    for source in SOURCES:
        effective_wcs[source] = _required_string(wcs, source, f"wcs.{source}")
        effective_coverage_ids[source] = _required_string(
            coverage_ids, source, f"coverage_ids.{source}"
        )
        version = versions.get(source, DEFAULT_WCS_VERSION)
        if not isinstance(version, str) or version not in SUPPORTED_WCS_VERSIONS:
            raise ValueError(
                f"wcs_versions.{source} is unsupported; expected one of {SUPPORTED_WCS_VERSIONS}"
            )
        effective_versions[source] = version
        if source in formats:
            configured_format = formats[source]
            if not isinstance(configured_format, str) or not configured_format.strip():
                raise ValueError(f"wcs_formats.{source} must be a non-empty string when supplied")
            effective_formats[source] = configured_format.strip()
        else:
            effective_formats[source] = DEFAULT_WCS_FORMATS[version]
    effective["wcs"] = effective_wcs
    effective["coverage_ids"] = effective_coverage_ids
    effective["wcs_versions"] = effective_versions
    effective["wcs_formats"] = effective_formats

    configured_thresholds = config.get("reporting_threshold_db", {})
    if not isinstance(configured_thresholds, Mapping):
        raise ValueError("reporting_threshold_db must be an object")
    effective_thresholds = {}
    for source in SOURCES:
        if source not in configured_thresholds:
            value = DEFAULT_REPORTING_THRESHOLDS[source]
        else:
            value = configured_thresholds[source]
            if source in ("road", "rail") and value is None:
                raise ValueError(f"reporting_threshold_db.{source} cannot be null")
        effective_thresholds[source] = (
            None if value is None else float(_number(value, f"reporting_threshold_db.{source}"))
        )
    effective["reporting_threshold_db"] = effective_thresholds

    configured_bounds = config.get("coverage_bounds_epsg27700", {})
    if not isinstance(configured_bounds, Mapping):
        raise ValueError("coverage_bounds_epsg27700 must be an object")
    effective_bounds = {}
    for source in SOURCES:
        if source in configured_bounds:
            effective_bounds[source] = _normalise_bounds(configured_bounds[source], source)
    effective["coverage_bounds_epsg27700"] = effective_bounds

    runner = config.get("runner", {})
    if not isinstance(runner, Mapping):
        raise ValueError("runner must be an object")
    for key in (
        "max_workers", "min_tile_start_interval_s", "max_attempts",
        "retry_base_backoff_s", "retry_max_backoff_s",
    ):
        if key in runner:
            _number(runner[key], f"runner.{key}")
    if "wcs_request_timeout_s" in config:
        _number(config["wcs_request_timeout_s"], "wcs_request_timeout_s")

    configured_mask = config.get("england_mask_100m_path") if "england_mask_100m_path" in config else None
    if mask_path is not None:
        explicit_mask = _resolve_existing_file(mask_path, "mask_path")
        if configured_mask is not None:
            configured_resolved = _resolve_existing_file(
                configured_mask, "england_mask_100m_path"
            )
            if configured_resolved != explicit_mask:
                raise ValueError(
                    "Configured england_mask_100m_path conflicts with the explicit mask_path"
                )
        effective["england_mask_100m_path"] = str(explicit_mask)
    elif configured_mask is not None:
        effective["england_mask_100m_path"] = str(
            _resolve_existing_file(configured_mask, "england_mask_100m_path")
        )
    elif require_mask:
        raise ValueError("An existing land mask is required for a production batch")

    return effective


def validate_production_config(
    config: Mapping,
    *,
    mask_path: str | Path | None = None,
    require_mask: bool = False,
) -> dict:
    """Compatibility alias emphasizing that normalization also validates."""
    return normalize_production_config(config, mask_path=mask_path, require_mask=require_mask)
