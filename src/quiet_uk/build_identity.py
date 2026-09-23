from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from pathlib import Path

from .config import normalize_production_config
from .source_grid import source_grid_policy_identity
from .validation import DEFAULT_NODATA, expected_band_schema


BUILD_IDENTITY_SCHEMA_VERSION = 1

# These are the Python files whose production behaviour contributes to a Phase
# 1 tile. Keep this list explicit so documentation, tests, and unrelated Phase
# 2 research code do not invalidate a resumable production build.
IMPLEMENTATION_FILES = (
    "src/quiet_uk/acoustics.py",
    "src/quiet_uk/build_identity.py",
    "src/quiet_uk/config.py",
    "src/quiet_uk/land_mask.py",
    "src/quiet_uk/locking.py",
    "src/quiet_uk/raster.py",
    "src/quiet_uk/runner.py",
    "src/quiet_uk/source_grid.py",
    "src/quiet_uk/tiling.py",
    "src/quiet_uk/validation.py",
    "src/quiet_uk/wcs.py",
)


def _implementation_hashes() -> dict[str, str]:
    module_path = Path(__file__).resolve()
    source_project_root = module_path.parents[2]
    installed_package_root = module_path.parent
    hashes = {}
    for relative in IMPLEMENTATION_FILES:
        path = source_project_root / relative
        if not path.is_file():
            # Source checkouts expose the explicit src/ path above; wheels do
            # not. Hash the corresponding installed package module so the
            # scientific identity remains available after wheel installation.
            package_relative = Path(relative).relative_to("src/quiet_uk")
            path = installed_package_root / package_relative
        if not path.is_file():
            raise ValueError(f"Relevant production implementation file is missing: {relative}")
        hashes[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    return hashes


def _mask_hash(mask_path: str | Path) -> str:
    path = Path(mask_path)
    if not path.is_file():
        raise ValueError(f"Land-mask file is missing: {path}")
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_build_identity_payload(
    config: Mapping,
    mask_path: str | Path,
    *,
    normalized: bool = False,
) -> dict:
    """Build the readable, deterministic scientific identity payload."""
    config = config if normalized else normalize_production_config(
        config, mask_path=mask_path, require_mask=True
    )
    sources = {}
    for source in ("road", "rail", "airport"):
        sources[source] = {
            "endpoint": config["wcs"][source],
            "coverage_id": config["coverage_ids"][source],
            "wcs_version": config["wcs_versions"][source],
            "format": config["wcs_formats"][source],
            "declared_coverage_bounds_epsg27700": config["coverage_bounds_epsg27700"].get(source),
        }

    return {
        "crs": config["crs"],
        "metric": config["metric"],
        "source_resolution_m": config["pilot_resolution_m"],
        "output_resolution_m": config["output_resolution_m"],
        "tile_size_m": config["tile_size_m"],
        "reporting_threshold_db": {
            source: config["reporting_threshold_db"][source]
            for source in ("road", "rail", "airport")
        },
        "sources": sources,
        "source_decoding": {
            "zero_is_censored": True,
            "declared_nodata_and_raster_mask_are_unreported": True,
            "unexpected_raw_nonfinite_values_rejected": True,
            "decoded_nonfinite_values_rejected": True,
        },
        "source_grid_policies": source_grid_policy_identity(),
        "alignment": {
            "grid_match_tolerance": 1e-6,
            "permitted_alignment": "only named source-grid policies; no unrestricted reprojection",
            "airport_padded_policy": "airport-wcs20-padded-native-grid v1",
            "alignment_resampling": "nearest-neighbour only within the documented airport policy",
            "target_grid": "exact tile core grid",
        },
        "output_dtype": "float32",
        "land_mask_sha256": _mask_hash(mask_path),
        "output_band_schema": expected_band_schema(config),
        "output_nodata": DEFAULT_NODATA,
        "implementation_files": _implementation_hashes(),
    }


def canonical_json(payload: Mapping) -> str:
    """Return the canonical JSON representation used for hashing."""
    try:
        return json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Build identity payload is not canonical JSON: {exc}") from exc


def fingerprint_payload(payload: Mapping) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def compute_build_identity(
    config: Mapping,
    mask_path: str | Path,
    *,
    normalized: bool = False,
) -> dict:
    payload = make_build_identity_payload(config, mask_path, normalized=normalized)
    return {
        "schema_version": BUILD_IDENTITY_SCHEMA_VERSION,
        "payload": payload,
        "fingerprint": fingerprint_payload(payload),
    }


def _flatten(value, prefix: str = "") -> dict[str, object]:
    if isinstance(value, Mapping):
        result = {}
        for key in sorted(value):
            result.update(_flatten(value[key], f"{prefix}.{key}" if prefix else str(key)))
        return result
    return {prefix: value}


def identity_differences(left: Mapping, right: Mapping, limit: int = 12) -> list[str]:
    """Describe changed payload paths for a useful resume error."""
    left_flat = _flatten(left)
    right_flat = _flatten(right)
    changed = sorted(set(left_flat) | set(right_flat))
    changed = [path for path in changed if left_flat.get(path) != right_flat.get(path)]
    return changed[:limit]
