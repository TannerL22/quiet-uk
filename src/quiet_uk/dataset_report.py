"""Read-only coverage, availability, and evidence reporting for Quiet UK."""
from __future__ import annotations

import hashlib
import json
import math
import os
import shutil
import tempfile
import time
import uuid
from collections import Counter, defaultdict
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import rasterio
from rasterio.windows import Window

from .catalogue import (
    BAND_DEPENDENCIES,
    BAND_QUALIFICATIONS,
    CATALOGUE_SCHEMA_VERSION,
    CATALOGUE_VALIDATION_CONTRACT_VERSION,
    CatalogueError,
    CatalogueIntegrityError,
    DatasetCatalogue,
    SOURCES,
    TARGET_CRS,
)
from .locking import ResourceLockError, resource_lock
from .tiling import TILE_BANDS
from .validation import DEFAULT_NODATA, SEMANTIC_TOLERANCE, validate_production_arrays


REPORT_SCHEMA_VERSION = 2
REPORT_JSON_NAME = "dataset_report.json"
REPORT_MARKDOWN_NAME = "dataset_report.md"
MASK_CELL_FOOTPRINT_AREA_KM2 = 0.01
QUALIFICATION_CATEGORIES = (
    "qualified",
    "qualified_with_coverage_limitation",
    "withheld_due_to_source_quality",
    "no_dataset_coverage",
)
SOURCE_STATE_CATEGORIES = (
    "grid_accepted",
    "grid_rejected",
    "outside_declared_coverage",
    "missing_evidence",
    "no_dataset_coverage",
)
AVAILABILITY_CATEGORIES = {
    "combined_reported_lower_db": (
        "finite_reported_value",
        "legitimate_censored_or_unreported_lower_bound",
        "no_dataset_coverage",
    ),
    "road_rail_upper_db": (
        "finite_stored_upper_bound",
        "no_dataset_coverage",
    ),
    "airport_reported_lower_db": (
        "finite_reported_value",
        "legitimate_censored_or_unreported_lower_bound",
        "no_dataset_coverage",
    ),
    "airport_reported_fraction": (
        "zero_reported_fraction",
        "positive_reported_fraction",
        "no_dataset_coverage",
    ),
}


class DatasetReportError(ValueError):
    """Raised when report inputs or output publication are invalid."""


class DatasetReportIntegrityError(RuntimeError):
    """Raised when validated catalogue or raster evidence cannot be trusted."""


def _parse_json_text(text: str, label: str) -> dict:
    duplicates: list[str] = []

    def hook(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                duplicates.append(str(key))
            result[key] = value
        return result

    try:
        value = json.loads(text, object_pairs_hook=hook)
    except json.JSONDecodeError as exc:
        raise DatasetReportError(f"Cannot parse {label}: {exc}") from exc
    if duplicates:
        raise DatasetReportError(f"{label} contains duplicate keys: {sorted(set(duplicates))}")
    if not isinstance(value, dict):
        raise DatasetReportError(f"{label} must be a JSON object")
    return value


def _json_load(path: Path, label: str) -> dict:
    try:
        return _parse_json_text(path.read_text(encoding="utf-8"), label)
    except OSError as exc:
        raise DatasetReportError(f"Cannot read {label} {path}: {exc}") from exc


def _resolve_file(path: str | Path, label: str) -> Path:
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise DatasetReportError(f"{label} is not available: {candidate}") from exc
    if not resolved.is_file():
        raise DatasetReportError(f"{label} is not a regular file: {resolved}")
    return resolved


def _resolve_directory(path: str | Path, label: str) -> Path:
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise DatasetReportError(f"{label} is not available: {candidate}") from exc
    if not resolved.is_dir():
        raise DatasetReportError(f"{label} is not a directory: {resolved}")
    return resolved


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise DatasetReportError(f"Cannot hash input {path}: {exc}") from exc
    return digest.hexdigest()


def _canonical(value) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False, default=str)


def _field_limitation(source: str, field: str, status: str) -> str:
    if status == "configured_only":
        return (
            "The value is recorded only in the supplied current configuration; "
            "that does not prove the setting used to create the historical rasters."
        )
    if status == "not_established":
        return "No value is recorded in the supplied historical inputs."
    if status == "conflicting":
        return "Supplied evidence conflicts; retain the disagreement until stronger historical or provider records resolve it."
    if field == "source_grid_policy":
        return "This records the structural reconciliation policy, not acoustic accuracy or provider semantics."
    return "Recorded in supplied historical evidence; independent provider semantics are not established by this report."


def _normalise_scalar(value, *, context: str):
    try:
        _canonical(value)
    except (TypeError, ValueError) as exc:
        raise DatasetReportIntegrityError(f"Invalid scalar evidence at {context}: {exc}") from exc
    return value


def _normalise_coverage_ids(value, *, context: str) -> tuple[str, ...]:
    if isinstance(value, str):
        identifiers = [value]
    elif isinstance(value, list):
        identifiers = value
    else:
        raise DatasetReportIntegrityError(
            f"Invalid coverage identifiers at {context}: expected a nonempty string or list of strings, got {type(value).__name__}"
        )
    if not identifiers:
        raise DatasetReportIntegrityError(f"Invalid coverage identifiers at {context}: the identifier list must not be empty")
    if any(not isinstance(identifier, str) for identifier in identifiers):
        raise DatasetReportIntegrityError(
            f"Invalid coverage identifiers at {context}: every identifier must be a nonempty string"
        )
    if any(not identifier.strip() for identifier in identifiers):
        raise DatasetReportIntegrityError(
            f"Invalid coverage identifiers at {context}: identifiers must not be empty or whitespace"
        )
    return tuple(sorted(set(identifiers)))


def _make_observation(
    input_key: str,
    input_identities: Mapping,
    *,
    tile_id: str | None,
    source: str,
    field_path: str,
    original_value,
    normalized_value,
) -> dict:
    identity = input_identities[input_key]
    return {
        "input": input_key,
        "sha256": identity["sha256"],
        "tile_id": tile_id,
        "source": source,
        "field_path": field_path,
        "original_value": original_value,
        "normalized_value": normalized_value,
    }


def _observation_reference(observation: Mapping) -> dict:
    return {
        "input": observation["input"],
        "sha256": observation["sha256"],
        "tile_id": observation["tile_id"],
        "source": observation["source"],
        "field_path": observation["field_path"],
    }


def _observation_sort_key(observation: Mapping) -> tuple[str, ...]:
    return (
        str(observation["input"]),
        "" if observation["tile_id"] is None else str(observation["tile_id"]),
        str(observation["source"]),
        str(observation["field_path"]),
        _canonical(observation["normalized_value"]),
        _canonical(observation["original_value"]),
    )


def _sorted_observations(observations: list[dict]) -> list[dict]:
    return sorted(observations, key=_observation_sort_key)


def _distinct_observations(observations: list[dict]) -> list[dict]:
    grouped = {}
    for observation in observations:
        grouped.setdefault(_canonical(observation["normalized_value"]), observation)
    return [grouped[key] for key in sorted(grouped)]


def _collapse_values(values: list[object], *, always_list: bool = False):
    ordered = sorted(values, key=_canonical)
    if always_list or len(ordered) != 1:
        return ordered
    return ordered[0] if ordered else None


def _unique_sorted_values(values: list[object]) -> list[object]:
    grouped = {}
    for value in values:
        grouped.setdefault(_canonical(value), value)
    return [grouped[key] for key in sorted(grouped)]


def _manifest_field_observations(
    manifest: Mapping,
    source: str,
    aliases: tuple[str, ...],
    input_identities: Mapping,
    *,
    coverage: bool = False,
) -> list[dict]:
    observations: list[dict] = []
    tiles = manifest.get("tiles")
    if not isinstance(tiles, Mapping):
        raise DatasetReportError("Historical manifest has no usable tiles object")
    for tile_id in sorted(tiles, key=str):
        record = tiles[tile_id]
        if not isinstance(record, Mapping):
            continue
        source_info = record.get("source_info", {})
        if not isinstance(source_info, Mapping):
            continue
        info = source_info.get(source, {})
        if not isinstance(info, Mapping):
            continue
        for alias in aliases:
            if alias not in info:
                continue
            value = info[alias]
            path = f"tiles.{tile_id}.source_info.{source}.{alias}"
            normalized = _normalise_coverage_ids(value, context=path) if coverage else _normalise_scalar(value, context=path)
            observations.append(
                _make_observation(
                    "manifest",
                    input_identities,
                    tile_id=str(tile_id),
                    source=source,
                    field_path=path,
                    original_value=value,
                    normalized_value=list(normalized) if coverage else normalized,
                )
            )
    return _sorted_observations(observations)


def _config_value(config: Mapping, source: str, field: str):
    if field == "coverage_ids":
        container = config.get("coverage_ids")
        path = f"coverage_ids.{source}"
    elif field == "wcs_version":
        container = config.get("wcs_versions")
        path = f"wcs_versions.{source}"
    elif field == "reporting_threshold_db":
        container = config.get("reporting_threshold_db")
        path = f"reporting_threshold_db.{source}"
    elif field == "declared_coverage_bounds_epsg27700":
        container = config.get("coverage_bounds_epsg27700")
        path = f"coverage_bounds_epsg27700.{source}"
    elif field == "provider_endpoint":
        container = config.get("wcs")
        path = f"wcs.{source}"
    elif field == "noise_metric":
        container = config.get("metric")
        path = "metric"
        if isinstance(container, Mapping):
            return (source in container, container.get(source), f"metric.{source}")
        return ("metric" in config, container, path)
    else:
        return False, None, path
    if not isinstance(container, Mapping):
        return False, None, path + " (not present)"
    return source in container, container.get(source), path if source in container else path + " (not present)"


def _reference_observations(manifest: Mapping, source: str, input_identities: Mapping) -> list[dict]:
    observations: list[dict] = []
    aliases = (
        ("year", ("reference_year", "year")),
        ("period", ("reference_period", "period")),
    )
    tiles = manifest.get("tiles")
    if not isinstance(tiles, Mapping):
        raise DatasetReportError("Historical manifest has no usable tiles object")
    for tile_id in sorted(tiles, key=str):
        record = tiles[tile_id]
        if not isinstance(record, Mapping):
            continue
        source_info = record.get("source_info", {})
        info = source_info.get(source, {}) if isinstance(source_info, Mapping) else {}
        if not isinstance(info, Mapping):
            continue
        for kind, kind_aliases in aliases:
            for alias in kind_aliases:
                if alias not in info:
                    continue
                path = f"tiles.{tile_id}.source_info.{source}.{alias}"
                value = info[alias]
                observations.append(
                    _make_observation(
                        "manifest",
                        input_identities,
                        tile_id=str(tile_id),
                        source=source,
                        field_path=path,
                        original_value=value,
                        normalized_value={"kind": kind, "value": _normalise_scalar(value, context=path)},
                    )
                )
    reference_years = manifest.get("reference_years")
    if isinstance(reference_years, Mapping) and source in reference_years:
        value = reference_years[source]
        path = f"reference_years.{source}"
        if isinstance(value, Mapping) and ("year" in value or "reference_year" in value or "period" in value or "reference_period" in value):
            for key in ("year", "reference_year", "period", "reference_period"):
                if key not in value:
                    continue
                kind = "year" if "year" in key else "period"
                subpath = f"{path}.{key}"
                observations.append(
                    _make_observation(
                        "manifest",
                        input_identities,
                        tile_id=None,
                        source=source,
                        field_path=subpath,
                        original_value=value[key],
                        normalized_value={"kind": kind, "value": _normalise_scalar(value[key], context=subpath)},
                    )
                )
        else:
            observations.append(
                _make_observation(
                    "manifest",
                    input_identities,
                    tile_id=None,
                    source=source,
                    field_path=path,
                    original_value=value,
                    normalized_value={"kind": "unspecified", "value": _normalise_scalar(value, context=path)},
                )
            )
    return _sorted_observations(observations)


def _configured_observations(
    config: Mapping,
    source: str,
    field: str,
    input_identities: Mapping,
    *,
    coverage: bool = False,
) -> tuple[bool, list[dict]]:
    present, value, path = _config_value(config, source, field)
    if not present:
        return False, []
    normalized = _normalise_coverage_ids(value, context=path) if coverage else _normalise_scalar(value, context=path)
    return True, [
        _make_observation(
            "production_config",
            input_identities,
            tile_id=None,
            source=source,
            field_path=path,
            original_value=value,
            normalized_value=list(normalized) if coverage else normalized,
        )
    ]


def _recorded_policy_observations(reconciliation_report: Mapping, source: str, input_identities: Mapping) -> list[dict]:
    rows = reconciliation_report.get("rows")
    if not isinstance(rows, list):
        raise DatasetReportError("Historical source-grid reconciliation report has no rows list")
    observations: list[dict] = []
    for index, row in enumerate(rows):
        if not isinstance(row, Mapping) or row.get("source") != source:
            continue
        tile_id = None if row.get("tile_id") is None else str(row["tile_id"])
        for key in ("policy_version", "policy_name"):
            if row.get(key) is None:
                continue
            path = f"rows[{index}].{key}"
            value = row[key]
            observations.append(
                _make_observation(
                    "reconciliation_report",
                    input_identities,
                    tile_id=tile_id,
                    source=source,
                    field_path=path,
                    original_value=value,
                    normalized_value=_normalise_scalar(value, context=path),
                )
            )
    return _sorted_observations(observations)


def _reference_display(observations: list[dict]):
    by_kind = defaultdict(list)
    for observation in observations:
        normalized = observation["normalized_value"]
        by_kind[normalized["kind"]].append(observation)
    result = {
        kind: _collapse_values(
            [item["original_value"] for item in _distinct_observations(kind_observations)]
        )
        for kind, kind_observations in sorted(by_kind.items())
    }
    if len(result) == 1:
        return next(iter(result.values()))
    return result


def _historical_summary(
    source: str,
    field: str,
    observations: list[dict],
    *,
    coverage: bool = False,
    reference: bool = False,
) -> tuple[object, bool, bool, set[str]]:
    by_tile = defaultdict(list)
    for observation in observations:
        by_tile[observation["tile_id"]].append(observation)

    if coverage:
        tile_sets = {}
        for tile_id, tile_observations in by_tile.items():
            distinct_sets = {
                tuple(observation["normalized_value"])
                for observation in tile_observations
            }
            if len(distinct_sets) > 1:
                references = [_observation_reference(item) for item in tile_observations]
                raise DatasetReportIntegrityError(
                    f"Conflicting identity-critical coverage identifiers for {source} in tile {tile_id}: "
                    f"sets={sorted(distinct_sets)!r}; evidence_references={references!r}"
                )
            tile_sets[tile_id] = next(iter(distinct_sets))
        union = set().union(*(set(values) for values in tile_sets.values())) if tile_sets else set()
        varies = len({_canonical(values) for values in tile_sets.values()}) > 1
        return sorted(union), varies, False, {_canonical(values) for values in tile_sets.values()}

    within_conflict = False
    tile_facts = set()
    for tile_id, tile_observations in by_tile.items():
        if reference:
            by_kind = defaultdict(list)
            for observation in tile_observations:
                normalized = observation["normalized_value"]
                by_kind[normalized["kind"]].append(observation)
            fact = {}
            for kind, kind_observations in by_kind.items():
                distinct = _distinct_observations(kind_observations)
                if len(distinct) > 1:
                    within_conflict = True
                fact[kind] = tuple(_canonical(item["normalized_value"]["value"]) for item in distinct)
            tile_facts.add(_canonical(fact))
        else:
            distinct = _distinct_observations(tile_observations)
            if len(distinct) > 1:
                within_conflict = True
            tile_facts.add(_canonical([item["normalized_value"] for item in distinct]))
    distinct = _distinct_observations(observations)
    display = _reference_display(observations) if reference else _collapse_values(
        [item["original_value"] for item in distinct]
    )
    varies = len(tile_facts) > 1
    normalized_keys = {_canonical(item["normalized_value"]) for item in distinct}
    return display, varies, within_conflict, normalized_keys


def _make_evidence_field(
    source: str,
    field: str,
    historical: list[dict],
    configured: list[dict],
    input_identities: Mapping,
    *,
    coverage: bool = False,
    reference: bool = False,
    identity_critical: bool = False,
) -> dict:
    historical = _sorted_observations(historical)
    configured = _sorted_observations(configured)
    historical_present = bool(historical)
    configured_present = bool(configured)
    historical_display = None
    historical_keys: set[str] = set()
    varies = False
    within_conflict = False
    tile_set_keys: set[str] = set()
    if historical_present:
        historical_display, varies, within_conflict, tile_set_keys = _historical_summary(
            source,
            field,
            historical,
            coverage=coverage,
            reference=reference,
        )
        if coverage:
            historical_keys = {_canonical(item) for item in historical_display}
        elif reference:
            historical_keys = {_canonical(item["normalized_value"]) for item in _distinct_observations(historical)}
        else:
            historical_keys = {_canonical(item["normalized_value"]) for item in _distinct_observations(historical)}

    configured_value = configured[0]["original_value"] if configured_present else None
    configured_normalized = configured[0]["normalized_value"] if configured_present else None
    configured_keys = set()
    if configured_present:
        configured_keys = (
            {_canonical(item) for item in configured_normalized}
            if coverage
            else {_canonical(configured_normalized)}
        )

    if identity_critical and historical_present and configured_present:
        comparable_historical = historical_keys if coverage else {
            _canonical(item["normalized_value"])
            for item in _distinct_observations(historical)
        }
        if comparable_historical != configured_keys:
            references = [_observation_reference(item) for item in historical + configured]
            historical_value = historical_display
            raise DatasetReportIntegrityError(
                f"Conflicting identity-critical evidence for {source} {field}: "
                f"historical={historical_value!r}, configured={configured_value!r}; "
                f"historical_normalized={sorted(comparable_historical)!r}, "
                f"configured_normalized={sorted(configured_keys)!r}; "
                f"evidence_references={references!r}"
            )

    if coverage:
        if historical_present:
            status = "recorded_in_historical_input"
            value = historical_display
        elif configured_present:
            status = "configured_only"
            value = sorted(configured_normalized)
        else:
            status = "not_established"
            value = None
    elif historical_present and configured_present:
        config_matches_historical = (
            historical_keys == {_canonical(configured_normalized)}
            if not reference
            else False
        )
        if within_conflict or not config_matches_historical:
            status = "conflicting"
            value = {"historical": historical_display, "configured": configured_value}
        else:
            status = "recorded_in_historical_input"
            value = historical_display
    elif historical_present:
        status = "conflicting" if within_conflict else "recorded_in_historical_input"
        value = {"historical": historical_display, "configured": None} if within_conflict else historical_display
    elif configured_present:
        status = "configured_only"
        value = configured_value
    else:
        status = "not_established"
        value = None

    observations = _sorted_observations(historical + configured)
    references = [_observation_reference(item) for item in observations]
    if not references:
        references = [{
            "input": "manifest",
            "sha256": input_identities["manifest"]["sha256"],
            "tile_id": None,
            "source": source,
            "field_path": f"tiles[*].source_info.{source}.{field} (not present)",
        }]
    return {
        "value": value,
        "evidence_status": status,
        "evidence_references": references,
        "limitation": _field_limitation(source, field, status),
        "varies_across_tiles": varies,
        "evidence_context": {
            "historical_present": historical_present,
            "historical_value": historical_display,
            "configured_present": configured_present,
            "configured_value": configured_value,
            "configured_normalized_value": configured_normalized,
            "historical_tile_value_keys": sorted(tile_set_keys),
        },
        "observations": observations,
    }


def _build_evidence_register(
    manifest: Mapping,
    reconciliation_report: Mapping,
    config: Mapping,
    input_identities: Mapping,
) -> dict:
    field_specs = {
        "coverage_identifiers": (("coverage_id", "coverage_ids"), "coverage_ids", True, False, True),
        "wcs_version": (("wcs_version", "wcs_versions"), "wcs_version", False, False, False),
        "noise_metric": (("metric", "noise_metric"), "noise_metric", False, False, False),
        "reporting_threshold_db": (("reporting_threshold_db", "reporting_threshold"), "reporting_threshold_db", False, False, False),
        "reference_year_or_period": (("reference_year", "reference_period", "year", "period"), None, False, True, False),
        "declared_coverage_bounds_epsg27700": (("declared_coverage_bounds", "coverage_bounds", "coverage_bounds_epsg27700"), "declared_coverage_bounds_epsg27700", False, False, False),
        "provider_endpoint": (("provider", "endpoint", "wcs", "url", "service_url"), "provider_endpoint", False, False, False),
    }
    register = {}
    for source in SOURCES:
        source_register = {}
        for field, (aliases, config_field, coverage, reference, identity_critical) in field_specs.items():
            historical = _reference_observations(manifest, source, input_identities) if reference else _manifest_field_observations(
                manifest,
                source,
                aliases,
                input_identities,
                coverage=coverage,
            )
            configured = []
            if config_field is not None:
                _, configured = _configured_observations(
                    config,
                    source,
                    config_field,
                    input_identities,
                    coverage=coverage,
                )
            source_register[field] = _make_evidence_field(
                source,
                field,
                historical,
                configured,
                input_identities,
                coverage=coverage,
                reference=reference,
                identity_critical=identity_critical,
            )

        policy_observations = _recorded_policy_observations(reconciliation_report, source, input_identities)
        if policy_observations:
            versions = [item["original_value"] for item in policy_observations if item["field_path"].endswith("policy_version")]
            names = [item["original_value"] for item in policy_observations if item["field_path"].endswith("policy_name")]
            tile_values = defaultdict(lambda: {"policy_versions": [], "policy_names": []})
            for item in policy_observations:
                key = "policy_versions" if item["field_path"].endswith("policy_version") else "policy_names"
                tile_values[item["tile_id"]][key].append(item["normalized_value"])
            tile_keys = {
                _canonical({key: _unique_sorted_values(values) for key, values in facts.items()})
                for facts in tile_values.values()
            }
            value = {
                "policy_versions": _collapse_values(_unique_sorted_values(versions), always_list=True),
                "policy_names": _collapse_values(_unique_sorted_values(names), always_list=True),
            }
            source_register["source_grid_policy"] = {
                "value": value,
                "evidence_status": "recorded_in_historical_input",
                "evidence_references": [_observation_reference(item) for item in policy_observations],
                "limitation": _field_limitation(source, "source_grid_policy", "recorded_in_historical_input"),
                "varies_across_tiles": len(tile_keys) > 1,
                "evidence_context": {
                    "historical_present": True,
                    "historical_value": value,
                    "configured_present": False,
                    "configured_value": None,
                    "configured_normalized_value": None,
                    "historical_tile_value_keys": sorted(tile_keys),
                },
                "observations": policy_observations,
            }
        else:
            source_register["source_grid_policy"] = {
                "value": None,
                "evidence_status": "not_established",
                "evidence_references": [
                    {
                        "input": "reconciliation_report",
                        "sha256": input_identities["reconciliation_report"]["sha256"],
                        "tile_id": None,
                        "source": source,
                        "field_path": f"rows[*].source={source}.policy_version/policy_name (not present)",
                    }
                ],
                "limitation": _field_limitation(source, "source_grid_policy", "not_established"),
                "varies_across_tiles": False,
                "evidence_context": {
                    "historical_present": False,
                    "historical_value": None,
                    "configured_present": False,
                    "configured_value": None,
                    "configured_normalized_value": None,
                    "historical_tile_value_keys": [],
                },
                "observations": [],
            }
        register[source] = source_register
    return register


def _validate_recorded_input(
    metadata: Mapping,
    input_key: str,
    path: Path,
    label: str,
) -> dict:
    recorded_inputs = metadata.get("inputs")
    recorded = recorded_inputs.get(input_key) if isinstance(recorded_inputs, Mapping) else None
    if not isinstance(recorded, Mapping) or not isinstance(recorded.get("sha256"), str):
        raise DatasetReportError(
            f"Catalogue does not record a required {input_key} identity; rebuild the catalogue with this input before reporting"
        )
    actual_size = path.stat().st_size
    actual_sha256 = _sha256_file(path)
    if recorded.get("file_size") is not None and int(recorded["file_size"]) != actual_size:
        raise DatasetReportIntegrityError(
            f"{label} size does not match catalogue identity: expected {recorded['file_size']}, got {actual_size}"
        )
    if actual_sha256 != recorded["sha256"]:
        raise DatasetReportIntegrityError(
            f"{label} checksum does not match catalogue identity: expected {recorded['sha256']}, got {actual_sha256}"
        )
    return {
        "path": str(path),
        "sha256": actual_sha256,
        "file_size": int(actual_size),
        "catalogue_recorded_path": recorded.get("path"),
        "matches_catalogue": True,
    }


def _float_tuple(value, length: int, label: str) -> tuple[float, ...]:
    if not isinstance(value, (list, tuple)) or len(value) != length:
        raise DatasetReportIntegrityError(f"{label} must contain {length} numeric values")
    try:
        result = tuple(float(item) for item in value)
    except (TypeError, ValueError) as exc:
        raise DatasetReportIntegrityError(f"{label} contains nonnumeric values") from exc
    if not all(math.isfinite(item) for item in result):
        raise DatasetReportIntegrityError(f"{label} contains non-finite values")
    return result


def _close_tuple(left, right, label: str, tolerance: float = 1e-9) -> None:
    if len(left) != len(right) or not np.allclose(left, right, rtol=0.0, atol=tolerance):
        raise DatasetReportIntegrityError(f"{label} disagrees with the authoritative mask grid")


def _validate_mask(dataset, expected_header: Mapping) -> dict:
    if dataset.count != 1:
        raise DatasetReportIntegrityError(f"England mask must have one band, got {dataset.count}")
    crs = dataset.crs.to_string() if dataset.crs is not None else None
    if crs != TARGET_CRS:
        raise DatasetReportIntegrityError(f"England mask CRS must be {TARGET_CRS}, got {crs!r}")
    transform = tuple(float(value) for value in dataset.transform)
    if (
        not math.isclose(transform[0], 100.0, rel_tol=0.0, abs_tol=1e-9)
        or not math.isclose(transform[4], -100.0, rel_tol=0.0, abs_tol=1e-9)
        or transform[1] != 0.0
        or transform[3] != 0.0
    ):
        raise DatasetReportIntegrityError("England mask must be an unrotated north-up 100 m EPSG:27700 grid")
    if dataset.dtypes[0] != "uint8":
        raise DatasetReportIntegrityError(f"England mask must use uint8 binary values, got {dataset.dtypes[0]}")
    if dataset.nodata is None or not math.isclose(float(dataset.nodata), 0.0, rel_tol=0.0, abs_tol=0.0):
        raise DatasetReportIntegrityError("England mask must use nodata representation 0")
    header = {
        "crs": crs,
        "dtype": dataset.dtypes[0],
        "nodata": float(dataset.nodata),
        "shape": [int(dataset.height), int(dataset.width)],
        "bounds": [float(value) for value in dataset.bounds],
        "transform": list(transform),
    }
    for key in ("crs", "dtype", "shape"):
        if expected_header.get(key) != header[key]:
            raise DatasetReportIntegrityError(f"England mask {key} disagrees with its recorded catalogue identity")
    try:
        expected_nodata = float(expected_header["nodata"])
    except (KeyError, TypeError, ValueError) as exc:
        raise DatasetReportIntegrityError("England mask nodata is missing from its recorded catalogue identity") from exc
    if not math.isclose(expected_nodata, header["nodata"], rel_tol=0.0, abs_tol=0.0):
        raise DatasetReportIntegrityError("England mask nodata disagrees with its recorded catalogue identity")
    _close_tuple(expected_header.get("bounds", ()), header["bounds"], "England mask bounds")
    _close_tuple(expected_header.get("transform", ()), header["transform"], "England mask transform")
    return header


def _count_mask_land(dataset) -> int:
    total = 0
    for _, window in dataset.block_windows(1):
        values = dataset.read(1, window=window, masked=False)
        if not np.all(np.isin(values, (0, 1))):
            invalid = np.unique(values[~np.isin(values, (0, 1))])[:5].tolist()
            raise DatasetReportIntegrityError(f"England mask contains non-binary values: {invalid}")
        total += int(np.count_nonzero(values > 0))
    return total


def _integer_grid_offset(value: float, label: str) -> int:
    rounded = int(round(value))
    if not math.isclose(value, rounded, rel_tol=0.0, abs_tol=1e-9):
        raise DatasetReportIntegrityError(f"{label} is not aligned to an integer mask-cell boundary: {value}")
    return rounded


def _tile_footprint(tile_row, mask_header: Mapping) -> dict:
    tile_id = str(tile_row["tile_id"])
    try:
        bounds = tuple(float(tile_row[key]) for key in ("minx", "miny", "maxx", "maxy"))
        width = int(tile_row["width"])
        height = int(tile_row["height"])
        transform = _float_tuple(json.loads(tile_row["transform_json"]), 9, f"Tile {tile_id} transform")
    except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise DatasetReportIntegrityError(f"Tile {tile_id} has malformed geometry metadata") from exc
    if width <= 0 or height <= 0:
        raise DatasetReportIntegrityError(f"Tile {tile_id} has nonpositive dimensions")
    if transform[1] != 0.0 or transform[3] != 0.0 or transform[0] != 100.0 or transform[4] != -100.0:
        raise DatasetReportIntegrityError(f"Tile {tile_id} is not on the authoritative north-up 100 m grid")
    expected_bounds = (transform[2], transform[5] + height * transform[4], transform[2] + width * transform[0], transform[5])
    _close_tuple(bounds, expected_bounds, f"Tile {tile_id} bounds/shape/transform")
    mask_transform = mask_header["transform"]
    left, top = float(mask_transform[2]), float(mask_transform[5])
    resolution = float(mask_transform[0])
    col_start = _integer_grid_offset((bounds[0] - left) / resolution, f"Tile {tile_id} west bound")
    col_end = _integer_grid_offset((bounds[2] - left) / resolution, f"Tile {tile_id} east bound")
    row_start = _integer_grid_offset((top - bounds[3]) / resolution, f"Tile {tile_id} north bound")
    row_end = _integer_grid_offset((top - bounds[1]) / resolution, f"Tile {tile_id} south bound")
    if (col_end - col_start, row_end - row_start) != (width, height):
        raise DatasetReportIntegrityError(f"Tile {tile_id} shape does not agree with mask-grid bounds")
    mask_height, mask_width = mask_header["shape"]
    if not (0 <= col_start < col_end <= mask_width and 0 <= row_start < row_end <= mask_height):
        raise DatasetReportIntegrityError(f"Tile {tile_id} footprint lies outside the authoritative mask grid")
    return {
        "tile_id": tile_id,
        "row_start": row_start,
        "row_end": row_end,
        "col_start": col_start,
        "col_end": col_end,
        "width": width,
        "height": height,
        "bounds": bounds,
        "transform": transform,
    }


def _validate_no_overlaps(footprints: list[dict]) -> None:
    active: list[dict] = []
    for footprint in sorted(footprints, key=lambda item: (item["row_start"], item["col_start"], item["tile_id"])):
        active = [item for item in active if item["row_end"] > footprint["row_start"]]
        for other in active:
            if max(other["col_start"], footprint["col_start"]) < min(other["col_end"], footprint["col_end"]):
                raise DatasetReportIntegrityError(
                    f"Overlapping catalogue tile footprints: {other['tile_id']} and {footprint['tile_id']}"
                )
        active.append(footprint)


def _stats(counts: Mapping[str, int], denominator: int) -> dict:
    result = {}
    for category, count in counts.items():
        count = int(count)
        result[category] = {
            "cell_count": count,
            "percentage_of_authoritative_land_cells": 0.0 if denominator == 0 else (100.0 * count / denominator),
            "mask_cell_footprint_area_km2": count * MASK_CELL_FOOTPRINT_AREA_KM2,
        }
    return result


def _initial_counter(categories: tuple[str, ...]) -> Counter:
    return Counter({category: 0 for category in categories})


def _availability_counts(band: str, values: np.ndarray, land: np.ndarray) -> Counter:
    categories = AVAILABILITY_CATEGORIES[band]
    result = _initial_counter(categories)
    land_values = values[land]
    if band in {"combined_reported_lower_db", "airport_reported_lower_db"}:
        censored = land_values == DEFAULT_NODATA
        finite = np.isfinite(land_values) & ~censored
        if int(np.count_nonzero(finite)) + int(np.count_nonzero(censored)) != int(land.sum()):
            raise DatasetReportIntegrityError(f"{band} contains an unclassifiable land-cell value")
        result["finite_reported_value"] = int(np.count_nonzero(finite))
        result["legitimate_censored_or_unreported_lower_bound"] = int(np.count_nonzero(censored))
    elif band == "road_rail_upper_db":
        finite = np.isfinite(land_values) & (land_values != DEFAULT_NODATA)
        if int(np.count_nonzero(finite)) != int(land.sum()):
            raise DatasetReportIntegrityError("road_rail_upper_db contains an unclassifiable land-cell value")
        result["finite_stored_upper_bound"] = int(np.count_nonzero(finite))
    else:
        zero = land_values == 0.0
        positive = land_values > 0.0
        if int(np.count_nonzero(zero)) + int(np.count_nonzero(positive)) != int(land.sum()):
            raise DatasetReportIntegrityError("airport_reported_fraction contains an unclassifiable land-cell value")
        result["zero_reported_fraction"] = int(np.count_nonzero(zero))
        result["positive_reported_fraction"] = int(np.count_nonzero(positive))
    return result


def _validate_tile_raster(tile_path: Path, tile_row, footprint: Mapping) -> np.ndarray:
    tile_id = str(tile_row["tile_id"])
    try:
        with rasterio.open(tile_path) as dataset:
            actual_transform = tuple(float(value) for value in dataset.transform)
            if dataset.count != len(TILE_BANDS) or list(dataset.dtypes) != ["float32"] * len(TILE_BANDS):
                raise DatasetReportIntegrityError(f"Tile {tile_id} raster band schema is not the frozen four-band schema")
            if dataset.width != footprint["width"] or dataset.height != footprint["height"]:
                raise DatasetReportIntegrityError(f"Tile {tile_id} raster shape disagrees with catalogue geometry")
            if dataset.crs is None or dataset.crs.to_string() != TARGET_CRS:
                raise DatasetReportIntegrityError(f"Tile {tile_id} raster CRS is not {TARGET_CRS}")
            _close_tuple(actual_transform, footprint["transform"], f"Tile {tile_id} raster transform")
            arrays = dataset.read(indexes=list(range(1, len(TILE_BANDS) + 1)), masked=False)
            descriptions = [description or "" for description in dataset.descriptions]
            if descriptions != list(TILE_BANDS):
                raise DatasetReportIntegrityError(f"Tile {tile_id} raster band names disagree with the catalogue schema")
            return arrays
    except DatasetReportIntegrityError:
        raise
    except (OSError, rasterio.errors.RasterioIOError) as exc:
        raise DatasetReportIntegrityError(f"Cannot read tile {tile_id}: {exc}") from exc


def _unresolved_evidence(evidence_register: Mapping) -> list[str]:
    unresolved = []
    for source in SOURCES:
        configured_only = [
            field.replace("_", " ")
            for field, record in evidence_register[source].items()
            if record["evidence_status"] == "configured_only"
        ]
        not_established = [
            field.replace("_", " ")
            for field, record in evidence_register[source].items()
            if record["evidence_status"] == "not_established"
        ]
        conflicting = [
            field.replace("_", " ")
            for field, record in evidence_register[source].items()
            if record["evidence_status"] == "conflicting"
        ]
        varying = [
            field.replace("_", " ")
            for field, record in evidence_register[source].items()
            if record.get("varies_across_tiles") is True
        ]
        if configured_only:
            unresolved.append(
                f"{source}: current configuration only records {', '.join(configured_only)}; provider or stronger historical records are needed to prove the settings used for the rasters."
            )
        if not_established:
            unresolved.append(f"{source}: {', '.join(not_established)} remain not established in the supplied evidence.")
        if conflicting:
            unresolved.append(f"{source}: conflicting evidence remains for {', '.join(conflicting)}.")
        if varying:
            unresolved.append(
                f"{source}: {', '.join(varying)} vary across tiles; this heterogeneity is retained as evidence context and is not treated as an error by itself."
            )
    unresolved.append("Reference years or periods are not inferred from download/build timestamps or coverage labels.")
    unresolved.append("Source-grid acceptance is structural compatibility evidence, not independent acoustic accuracy evidence.")
    return unresolved


def _build_markdown(report: Mapping) -> str:
    def value_text(value) -> str:
        if value is None:
            return "null"
        return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

    def number(value) -> str:
        return f"{float(value):.6f}"

    lines = [
        "# Quiet UK dataset coverage and evidence report",
        "",
        f"- Report ID: `{report['report_id']}`",
        f"- Generated at (UTC): `{report['generated_at_utc']}`",
        f"- Catalogue build ID: `{report['catalogue']['build_id']}`",
        f"- Report schema version: `{report['report_schema_version']}`",
        "",
        "This is a read-only audit of the supplied historical England catalogue. Structural source qualification is not acoustic accuracy, and stored-value availability is not the same as qualified usability.",
        "",
        "## Inputs and identities",
        "",
        "| Input | SHA-256 | Matches catalogue |",
        "|---|---|---|",
    ]
    for name, identity in report["input_identities"].items():
        if "sha256" not in identity:
            continue
        lines.append(f"| `{name}` | `{identity['sha256']}` | `{identity.get('matches_catalogue', False)}` |")
    lines.extend([
        "",
        "## Authoritative land denominator",
        "",
        "The denominator is the positive-cell count from the complete supplied England mask, including land cells outside all catalogue tiles. Areas below are mask-cell footprint areas (`cell_count * 0.01 km²`), not exact physical land area; coastal mask cells may contain only part land.",
        "",
        "| Category | Cells | % of authoritative land | Mask-cell footprint km² |",
        "|---|---:|---:|---:|",
    ])
    denominator = report["mask_denominator"]
    for category in ("total_authoritative_land", "owned_by_catalogue_tile", "no_catalogue_tile"):
        item = denominator[category]
        lines.append(f"| `{category}` | {item['cell_count']:,} | {number(item['percentage_of_authoritative_land_cells'])}% | {number(item['mask_cell_footprint_area_km2'])} |")
    lines.extend(["", "## Per-band coverage", ""])
    for band, band_report in report["per_band_coverage"].items():
        lines.extend([
            f"### `{band}`",
            "",
            "| Category | Cells | % of authoritative land | Mask-cell footprint km² |",
            "|---|---:|---:|---:|",
        ])
        for category, item in band_report["categories"].items():
            lines.append(f"| `{category}` | {item['cell_count']:,} | {number(item['percentage_of_authoritative_land_cells'])}% | {number(item['mask_cell_footprint_area_km2'])} |")
        lines.extend(["", f"Reconciles to denominator: `{band_report['reconciles_to_authoritative_land']}`.", ""])
    lines.extend([
        "## Structural source-quality states",
        "",
        "These land-cell counts inherit each catalogue tile's source state. They describe structural evidence and coverage handling, not acoustic accuracy.",
        "",
        "| Source | State | Cells | % of authoritative land |",
        "|---|---|---:|---:|",
    ])
    for source, source_report in report["source_state_land_cells"].items():
        for state, item in source_report["categories"].items():
            lines.append(f"| `{source}` | `{state}` | {item['cell_count']:,} | {number(item['percentage_of_authoritative_land_cells'])}% |")
    lines.extend(["", "## Stored-value availability", "", "Availability is counted separately from source qualification. The cross-tabs keep finite stored values in withheld tiles visible without presenting them as qualified.", ""])
    for band, availability_report in report["value_availability"].items():
        lines.extend([
            f"### `{band}`",
            "",
            "| Availability category | Cells | % of authoritative land |",
            "|---|---:|---:|",
        ])
        for category, item in availability_report["categories"].items():
            lines.append(f"| `{category}` | {item['cell_count']:,} | {number(item['percentage_of_authoritative_land_cells'])}% |")
        columns = list(availability_report["categories"])
        lines.extend(["", "| Qualification \\ Availability | " + " | ".join(f"`{column}`" for column in columns) + " | Total |", "|---|" + "---:|" * (len(columns) + 1)])
        for qualification, cells in availability_report["by_qualification"].items():
            total = sum(cells.values())
            lines.append("| `" + qualification + "` | " + " | ".join(f"{cells.get(column, 0):,}" for column in columns) + f" | {total:,} |")
        lines.append("")
    lines.extend(["## Source evidence register", "", "Evidence status values are `recorded_in_historical_input`, `configured_only`, `not_established`, and `conflicting`. Current configuration values are not treated as proof of historical raster creation settings.", ""])
    def reference_text(record: Mapping) -> str:
        references = record.get("evidence_references", [])
        if not references:
            return "none"
        if len(references) <= 3:
            return "; ".join(
                f"{item.get('input')}:{item.get('tile_id') or '*'}:{item.get('field_path')}"
                for item in references
            ).replace("|", "\\|")
        tile_ids = sorted({item.get("tile_id") for item in references if item.get("tile_id") is not None})
        tile_summary = ""
        if tile_ids:
            tile_summary = f"; tiles {tile_ids[0]}…{tile_ids[-1]}"
        return f"{len(references)} references{tile_summary} (full list in JSON)"

    for source, fields in report["evidence_register"].items():
        lines.extend([
            f"### `{source}`",
            "",
            "| Field | Value | Status | Varies across tiles | Historical/configured context | References | Limitation |",
            "|---|---|---|---|---|---|---|",
        ])
        for field, record in fields.items():
            limitation = record["limitation"].replace("|", "\\|")
            context = record.get("evidence_context", {})
            context_text = (
                f"historical={value_text(context.get('historical_value'))}; "
                f"configured={'present' if context.get('configured_present') else 'absent'}"
                f"({value_text(context.get('configured_value'))})"
            ).replace("|", "\\|")
            lines.append(
                f"| `{field}` | `{value_text(record['value'])}` | `{record['evidence_status']}` | "
                f"`{record.get('varies_across_tiles', False)}` | `{context_text}` | {reference_text(record)} | {limitation} |"
            )
        lines.append("")
    lines.extend(["## Unresolved evidence", ""])
    lines.extend(f"- {item}" for item in report["unresolved_evidence"])
    lines.extend([
        "",
        "## Processing",
        "",
        f"- Processed tiles: `{report['processing']['processed_tile_count']:,}`",
        f"- Processed tile cells: `{report['processing']['processed_tile_cells']:,}`",
        f"- Processed catalogue-owned land cells: `{report['processing']['processed_owned_land_cells']:,}`",
        f"- Authoritative mask land cells counted: `{report['processing']['authoritative_land_cells_counted']:,}`",
        f"- Elapsed report-generation time: `{report['processing']['elapsed_seconds']:.6f}` seconds",
        "",
        "No minimum-noise, ranking, average-dB, candidate-area, or silence interpretation is produced by this report.",
        "",
    ])
    return "\n".join(lines)


def _serialize_report_json(report: Mapping) -> str:
    return json.dumps(report, indent=2, sort_keys=False, allow_nan=False) + "\n"


def _serialize_report_markdown(report: Mapping) -> str:
    return _build_markdown(report)


def _prepare_output(output: Path) -> None:
    if output.exists() and not output.is_dir():
        raise DatasetReportError(f"Report output path is not a directory: {output}")
    if output.exists() and any(output.iterdir()):
        raise FileExistsError(f"Report output directory is not empty: {output}")
    output.mkdir(parents=True, exist_ok=True)


def _publish_report_pair(staged_json: Path, staged_markdown: Path, output: Path) -> None:
    published: list[Path] = []
    try:
        json_path = output / REPORT_JSON_NAME
        markdown_path = output / REPORT_MARKDOWN_NAME
        os.replace(staged_json, json_path)
        published.append(json_path)
        os.replace(staged_markdown, markdown_path)
        published.append(markdown_path)
    except OSError as exc:
        for path in published:
            try:
                path.unlink()
            except OSError:
                pass
        raise DatasetReportError(f"Dataset report publication failed; no completed report pair was left: {exc}") from exc


def generate_dataset_report(
    catalogue_dir: str | Path,
    tile_dir: str | Path,
    land_mask_path: str | Path,
    manifest_path: str | Path,
    reconciliation_report_path: str | Path,
    config_path: str | Path,
    output_dir: str | Path,
) -> dict:
    """Generate and publish a read-only coverage/evidence report."""
    started = time.perf_counter()
    output = Path(output_dir).resolve()
    try:
        with resource_lock(output, "dataset report output"):
            _prepare_output(output)
            manifest_file = _resolve_file(manifest_path, "Historical manifest")
            reconciliation_file = _resolve_file(reconciliation_report_path, "Historical source-grid reconciliation report")
            mask_file = _resolve_file(land_mask_path, "Authoritative England land mask")
            config_file = _resolve_file(config_path, "Production configuration")
            tile_root = _resolve_directory(tile_dir, "Catalogue tile directory")
            with DatasetCatalogue(catalogue_dir, tile_root, mask_file) as catalogue:
                metadata = catalogue.metadata
                input_identities = {
                    "manifest": _validate_recorded_input(metadata, "manifest", manifest_file, "Historical manifest"),
                    "reconciliation_report": _validate_recorded_input(metadata, "reconciliation_report", reconciliation_file, "Reconciliation report"),
                    "land_mask": _validate_recorded_input(metadata, "land_mask", mask_file, "England land mask"),
                    "production_config": _validate_recorded_input(metadata, "production_config", config_file, "Production configuration"),
                    "tile_directory": {
                        "path": str(tile_root),
                        "sha256": None,
                        "matches_catalogue": True,
                        "catalogue_recorded_path": metadata.get("inputs", {}).get("tile_directory", {}).get("path"),
                    },
                }
                manifest = _json_load(manifest_file, "historical manifest")
                reconciliation_report = _json_load(reconciliation_file, "source-grid reconciliation report")
                config = _json_load(config_file, "production configuration")
                evidence_register = _build_evidence_register(
                    manifest,
                    reconciliation_report,
                    config,
                    input_identities,
                )
                expected_mask = metadata.get("inputs", {}).get("land_mask", {}).get("header", {})
                if not isinstance(expected_mask, Mapping):
                    raise DatasetReportError("Catalogue lacks the recorded authoritative mask header")
                with rasterio.open(mask_file) as mask_dataset:
                    mask_header = _validate_mask(mask_dataset, expected_mask)
                    total_land_cells = _count_mask_land(mask_dataset)
                    tile_records = list(catalogue.iter_tile_records())
                    footprints_by_id = {
                        str(tile_row["tile_id"]): _tile_footprint(tile_row, mask_header)
                        for tile_row, _, _ in tile_records
                    }
                    _validate_no_overlaps(list(footprints_by_id.values()))

                    owned_land_cells = 0
                    processed_tile_cells = 0
                    source_counts = {
                        source: _initial_counter(SOURCE_STATE_CATEGORIES)
                        for source in SOURCES
                    }
                    coverage_counts = {
                        band: _initial_counter(QUALIFICATION_CATEGORIES)
                        for band in TILE_BANDS
                    }
                    availability_counts = {
                        band: _initial_counter(AVAILABILITY_CATEGORIES[band])
                        for band in TILE_BANDS
                    }
                    cross_tabs = {
                        band: {
                            qualification: _initial_counter(AVAILABILITY_CATEGORIES[band])
                            for qualification in QUALIFICATION_CATEGORIES
                        }
                        for band in TILE_BANDS
                    }
                    for tile_row, source_rows, band_rows in tile_records:
                        tile_id = str(tile_row["tile_id"])
                        footprint = footprints_by_id[tile_id]
                        tile_path = catalogue.verified_tile_path(tile_row)
                        arrays = _validate_tile_raster(tile_path, tile_row, footprint)
                        mask_window = Window(
                            footprint["col_start"],
                            footprint["row_start"],
                            footprint["width"],
                            footprint["height"],
                        )
                        land = mask_dataset.read(1, window=mask_window, masked=False) > 0
                        if land.shape != arrays.shape[1:]:
                            raise DatasetReportIntegrityError(f"Mask window shape disagrees with tile {tile_id}")
                        try:
                            validate_production_arrays(
                                arrays,
                                land,
                                nodata=DEFAULT_NODATA,
                                band_names=TILE_BANDS,
                                config=catalogue.semantic_config,
                            )
                        except (TypeError, ValueError) as exc:
                            raise DatasetReportIntegrityError(
                                f"Tile {tile_id} semantic integrity validation failed during reporting: {exc}"
                            ) from exc
                        land_cells = int(np.count_nonzero(land))
                        owned_land_cells += land_cells
                        processed_tile_cells += int(footprint["width"] * footprint["height"])
                        source_by_name = {str(row["source"]): row for row in source_rows}
                        band_by_name = {str(row["band_name"]): row for row in band_rows}
                        for source in SOURCES:
                            state = str(source_by_name[source]["state"])
                            if state not in SOURCE_STATE_CATEGORIES[:-1]:
                                raise DatasetReportIntegrityError(f"Unsupported source state {state!r} for {tile_id}/{source}")
                            source_counts[source][state] += land_cells
                        for band_index, band in enumerate(TILE_BANDS):
                            qualification = str(band_by_name[band]["qualification"])
                            if qualification not in QUALIFICATION_CATEGORIES[:-1]:
                                raise DatasetReportIntegrityError(f"Unsupported band qualification {qualification!r} for {tile_id}/{band}")
                            coverage_counts[band][qualification] += land_cells
                            tile_availability = _availability_counts(band, arrays[band_index], land)
                            for category, count in tile_availability.items():
                                availability_counts[band][category] += count
                                cross_tabs[band][qualification][category] += count

                if owned_land_cells > total_land_cells:
                    raise DatasetReportIntegrityError(
                        f"Catalogue-owned land cells {owned_land_cells} exceed authoritative mask land cells {total_land_cells}"
                    )
                uncovered_land_cells = total_land_cells - owned_land_cells
                for source in SOURCES:
                    source_counts[source]["no_dataset_coverage"] = uncovered_land_cells
                for band in TILE_BANDS:
                    coverage_counts[band]["no_dataset_coverage"] = uncovered_land_cells
                    availability_counts[band]["no_dataset_coverage"] = uncovered_land_cells
                    cross_tabs[band]["no_dataset_coverage"]["no_dataset_coverage"] = uncovered_land_cells

                per_band_coverage = {}
                value_availability = {}
                for band in TILE_BANDS:
                    band_total = sum(coverage_counts[band].values())
                    if band_total != total_land_cells:
                        raise DatasetReportIntegrityError(f"{band} coverage does not reconcile to the authoritative land denominator")
                    availability_total = sum(availability_counts[band].values())
                    if availability_total != total_land_cells:
                        raise DatasetReportIntegrityError(f"{band} value availability does not reconcile to the authoritative land denominator")
                    per_band_coverage[band] = {
                        "categories": _stats(coverage_counts[band], total_land_cells),
                        "reconciles_to_authoritative_land": True,
                    }
                    value_availability[band] = {
                        "categories": _stats(availability_counts[band], total_land_cells),
                        "by_qualification": {
                            qualification: dict(cross_tabs[band][qualification])
                            for qualification in QUALIFICATION_CATEGORIES
                        },
                        "reconciles_to_authoritative_land": True,
                    }

                source_state_land_cells = {
                    source: {
                        "categories": _stats(source_counts[source], total_land_cells),
                        "reconciles_to_authoritative_land": sum(source_counts[source].values()) == total_land_cells,
                        "structural_evidence_only": True,
                    }
                    for source in SOURCES
                }
                if not all(item["reconciles_to_authoritative_land"] for item in source_state_land_cells.values()):
                    raise DatasetReportIntegrityError("Source-state land-cell counts do not reconcile to the authoritative land denominator")

                generated_at = datetime.now(timezone.utc).isoformat()
                report = {
                    "report_schema_version": REPORT_SCHEMA_VERSION,
                    "report_id": uuid.uuid4().hex,
                    "generated_at_utc": generated_at,
                    "catalogue": {
                        "directory": str(catalogue.catalogue_dir),
                        "build_id": metadata["build_id"],
                        "catalogue_schema_version": metadata["catalogue_schema_version"],
                        "catalogue_validation_contract_version": metadata["catalogue_validation_contract_version"],
                        "catalogue_publication_contract_version": metadata["catalogue_publication_contract_version"],
                    },
                    "input_identities": input_identities,
                    "mask_denominator": {
                        "total_authoritative_land": {
                            "cell_count": total_land_cells,
                            "percentage_of_authoritative_land_cells": 100.0 if total_land_cells else 0.0,
                            "mask_cell_footprint_area_km2": total_land_cells * MASK_CELL_FOOTPRINT_AREA_KM2,
                        },
                        "owned_by_catalogue_tile": {
                            "cell_count": owned_land_cells,
                            "percentage_of_authoritative_land_cells": 0.0 if total_land_cells == 0 else 100.0 * owned_land_cells / total_land_cells,
                            "mask_cell_footprint_area_km2": owned_land_cells * MASK_CELL_FOOTPRINT_AREA_KM2,
                        },
                        "no_catalogue_tile": {
                            "cell_count": uncovered_land_cells,
                            "percentage_of_authoritative_land_cells": 0.0 if total_land_cells == 0 else 100.0 * uncovered_land_cells / total_land_cells,
                            "mask_cell_footprint_area_km2": uncovered_land_cells * MASK_CELL_FOOTPRINT_AREA_KM2,
                        },
                        "mask_header": mask_header,
                        "mask_cell_footprint_area_km2_per_cell": MASK_CELL_FOOTPRINT_AREA_KM2,
                        "area_caveat": "Mask-cell footprint area is not exact physical land area because coastal mask cells may contain only part land.",
                    },
                    "per_band_coverage": per_band_coverage,
                    "source_state_land_cells": source_state_land_cells,
                    "value_availability": value_availability,
                    "evidence_register": evidence_register,
                    "unresolved_evidence": _unresolved_evidence(evidence_register),
                    "processing": {
                        "processed_tile_count": len(tile_records),
                        "processed_tile_cells": processed_tile_cells,
                        "processed_owned_land_cells": owned_land_cells,
                        "authoritative_land_cells_counted": total_land_cells,
                        "elapsed_seconds": 0.0,
                    },
                }
            report["processing"]["elapsed_seconds"] = time.perf_counter() - started
            serialized_json = _serialize_report_json(report)
            markdown = _serialize_report_markdown(report)
            staging_dir = Path(tempfile.mkdtemp(prefix=".dataset-report-build-", dir=output))
            try:
                staged_json = staging_dir / REPORT_JSON_NAME
                staged_markdown = staging_dir / REPORT_MARKDOWN_NAME
                staged_json.write_text(serialized_json, encoding="utf-8")
                staged_markdown.write_text(markdown, encoding="utf-8")
                parsed = _parse_json_text(staged_json.read_text(encoding="utf-8"), "staged dataset report JSON")
                if parsed != report:
                    raise DatasetReportIntegrityError("Serialized dataset report does not match its report object")
                _publish_report_pair(staged_json, staged_markdown, output)
            finally:
                shutil.rmtree(staging_dir, ignore_errors=True)
            return {
                "report_dir": str(output),
                "json": str(output / REPORT_JSON_NAME),
                "markdown": str(output / REPORT_MARKDOWN_NAME),
                "report_payload": report,
            }
    except ResourceLockError as exc:
        raise DatasetReportError(f"Dataset report output is locked or unavailable: {output}") from exc
    except CatalogueIntegrityError as exc:
        raise DatasetReportIntegrityError(str(exc)) from exc
    except CatalogueError as exc:
        raise DatasetReportError(str(exc)) from exc


def build_dataset_report(*args, **kwargs) -> dict:
    """Compatibility spelling for :func:`generate_dataset_report`."""
    return generate_dataset_report(*args, **kwargs)
