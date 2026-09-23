"""Read-only catalogue construction and point lookup for the England tiles.

The catalogue is deliberately a derived product.  It validates the supplied
historical evidence and tile headers, writes a small SQLite index plus JSON
metadata, and never calls acquisition or production-processing code.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import re
import shutil
import sqlite3
import tempfile
import uuid
from collections import Counter
from collections.abc import Mapping
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import numpy as np
import rasterio
from rasterio.transform import Affine
from rasterio.warp import transform as transform_coords
from rasterio.windows import Window

from .config import DEFAULT_REPORTING_THRESHOLDS, SUPPORTED_WCS_VERSIONS
from .land_mask import read_tile_land_mask
from .locking import ResourceLockError, resource_lock
from .source_grid import (
    AIRPORT_PADDED_GRID_POLICY,
    EXACT_GRID_POLICY,
    GRID_POLICY_VERSION,
    validate_source_grid,
)
from .tiling import TILE_BANDS, Tile, tile_grid
from .validation import DEFAULT_NODATA, validate_production_arrays


CATALOGUE_SCHEMA_VERSION = 2
CATALOGUE_VALIDATION_CONTRACT_VERSION = 2
CATALOGUE_PUBLICATION_CONTRACT_VERSION = 1
CATALOGUE_JSON_NAME = "catalogue.json"
CATALOGUE_DB_NAME = "catalogue.sqlite3"
TARGET_CRS = "EPSG:27700"
WGS84_CRS = "EPSG:4326"
TILE_FILENAME_PATTERN = re.compile(r"^(r\d{4}c\d{4})\.tif$")
SOURCES = ("road", "rail", "airport")
LOWER_BOUND_BANDS = frozenset({"combined_reported_lower_db", "airport_reported_lower_db"})

BAND_DEPENDENCIES = {
    "combined_reported_lower_db": ("road", "rail", "airport"),
    "road_rail_upper_db": ("road", "rail"),
    "airport_reported_lower_db": ("airport",),
    "airport_reported_fraction": ("airport",),
}
BAND_DICTIONARY = {
    "combined_reported_lower_db": {
        "unit": "dB",
        "meaning": "Reported road, rail and airport energy combined as a lower bound.",
        "dependencies": list(BAND_DEPENDENCIES["combined_reported_lower_db"]),
        "interpretation": "Not a total ambient measurement and not an upper bound.",
    },
    "road_rail_upper_db": {
        "unit": "dB",
        "meaning": "Conservative road and rail upper bound using configured censoring floors.",
        "dependencies": list(BAND_DEPENDENCIES["road_rail_upper_db"]),
        "interpretation": "Does not include airport noise and is not paired with the combined lower band.",
    },
    "airport_reported_lower_db": {
        "unit": "dB",
        "meaning": "Reported airport energy as a lower bound.",
        "dependencies": list(BAND_DEPENDENCIES["airport_reported_lower_db"]),
        "interpretation": "Outside airport coverage is not evidence of no aircraft noise.",
    },
    "airport_reported_fraction": {
        "unit": "fraction",
        "meaning": "Fraction of source cells in the output cell with a reported airport value.",
        "dependencies": list(BAND_DEPENDENCIES["airport_reported_fraction"]),
        "interpretation": "Zero means no reported airport pixels, not silence.",
    },
}
SOURCE_STATES = (
    "grid_accepted",
    "grid_rejected",
    "outside_declared_coverage",
    "missing_evidence",
)
BAND_QUALIFICATIONS = (
    "qualified",
    "qualified_with_coverage_limitation",
    "withheld_due_to_source_quality",
)


class CatalogueError(ValueError):
    """Base error for invalid catalogue inputs or queries."""


class CatalogueIntegrityError(RuntimeError):
    """Raised when catalogue-described bytes or metadata cannot be trusted."""


class CataloguePublicationError(CatalogueError):
    """Raised when a completed catalogue cannot be published as a pair."""


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
        raise CatalogueError(f"Cannot parse JSON metadata {label}: {exc}") from exc
    if duplicates:
        raise CatalogueError(f"JSON metadata {label} contains duplicate keys: {sorted(set(duplicates))}")
    if not isinstance(value, dict):
        raise CatalogueError(f"JSON metadata must be an object: {label}")
    return value


def _json_load(path: Path) -> dict:
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise CatalogueError(f"Cannot read JSON input {path}: {exc}") from exc
    return _parse_json_text(text, str(path))


_REQUIRED_METADATA_KEYS = frozenset({
    "catalogue_schema_version",
    "catalogue_validation_contract_version",
    "catalogue_publication_contract_version",
    "build_id",
    "dataset_label",
    "build_timestamp_utc",
    "grid",
    "band_dictionary",
    "semantic_validation",
    "inputs",
    "tile_count",
    "quality_counts",
    "reference_years",
    "limitations",
})


def _validate_metadata_contract(metadata: Mapping, label: str) -> dict:
    missing = sorted(_REQUIRED_METADATA_KEYS - set(metadata))
    if missing:
        raise CatalogueError(
            f"{label} is missing publication metadata {missing}; rebuild with scripts/19_build_catalogue.py"
        )
    if metadata.get("catalogue_schema_version") != CATALOGUE_SCHEMA_VERSION:
        raise CatalogueError(
            f"{label} has unsupported catalogue schema {metadata.get('catalogue_schema_version')!r}; "
            "rebuild with scripts/19_build_catalogue.py"
        )
    if metadata.get("catalogue_validation_contract_version") != CATALOGUE_VALIDATION_CONTRACT_VERSION:
        raise CatalogueError(
            f"{label} uses an unsupported scientific validation contract; "
            "rebuild with scripts/19_build_catalogue.py"
        )
    if metadata.get("catalogue_publication_contract_version") != CATALOGUE_PUBLICATION_CONTRACT_VERSION:
        raise CatalogueError(
            f"{label} uses an unsupported publication contract; rebuild with scripts/19_build_catalogue.py"
        )
    if not isinstance(metadata.get("build_id"), str) or not metadata["build_id"].strip():
        raise CatalogueError(
            f"{label} has no valid build identifier; rebuild with scripts/19_build_catalogue.py"
        )
    if not isinstance(metadata.get("dataset_label"), str) or not metadata["dataset_label"].strip():
        raise CatalogueError(f"{label} has no valid dataset label; rebuild with scripts/19_build_catalogue.py")
    if not isinstance(metadata.get("inputs"), Mapping):
        raise CatalogueError(f"{label} has malformed input metadata; rebuild with scripts/19_build_catalogue.py")
    if not isinstance(metadata.get("reference_years"), Mapping):
        raise CatalogueError(f"{label} has malformed reference-year metadata; rebuild with scripts/19_build_catalogue.py")
    if not isinstance(metadata.get("quality_counts"), Mapping):
        raise CatalogueError(f"{label} has malformed quality-count metadata; rebuild with scripts/19_build_catalogue.py")
    try:
        tile_count = int(metadata["tile_count"])
    except (TypeError, ValueError) as exc:
        raise CatalogueError(f"{label} has malformed tile-count metadata; rebuild with scripts/19_build_catalogue.py") from exc
    if isinstance(metadata["tile_count"], bool) or tile_count < 0:
        raise CatalogueError(f"{label} has malformed tile-count metadata; rebuild with scripts/19_build_catalogue.py")
    semantic = metadata.get("semantic_validation")
    if not isinstance(semantic, Mapping) or semantic.get("contract_version") != CATALOGUE_VALIDATION_CONTRACT_VERSION:
        raise CatalogueError(
            f"{label} has incomplete semantic-validation metadata; rebuild with scripts/19_build_catalogue.py"
        )
    try:
        nodata = float(semantic.get("nodata"))
    except (TypeError, ValueError) as exc:
        raise CatalogueError(f"{label} has malformed semantic-validation metadata; rebuild with scripts/19_build_catalogue.py") from exc
    if not math.isclose(nodata, DEFAULT_NODATA, rel_tol=0.0, abs_tol=0.0):
        raise CatalogueError(
            f"{label} has an unsupported semantic nodata value; rebuild with scripts/19_build_catalogue.py"
        )
    if not isinstance(semantic.get("reporting_threshold_db"), Mapping):
        raise CatalogueError(
            f"{label} has malformed semantic thresholds; rebuild with scripts/19_build_catalogue.py"
        )
    return dict(metadata)


def _read_authoritative_metadata(connection: sqlite3.Connection, label: str) -> dict:
    try:
        rows = connection.execute(
            "SELECT value_json FROM catalogue_info WHERE key = ?",
            ("metadata",),
        ).fetchall()
    except sqlite3.Error as exc:
        raise CatalogueError(
            f"{label} has no readable authoritative metadata; rebuild with scripts/19_build_catalogue.py"
        ) from exc
    if len(rows) != 1:
        raise CatalogueError(
            f"{label} has {len(rows)} authoritative metadata rows; rebuild with scripts/19_build_catalogue.py"
        )
    return _validate_metadata_contract(
        _parse_json_text(str(rows[0][0]), f"{label} SQLite catalogue_info"),
        f"{label} authoritative metadata",
    )


def _validate_database_counts(connection: sqlite3.Connection, metadata: Mapping, label: str) -> None:
    tile_count = int(metadata["tile_count"])
    expected_counts = {
        "tiles": tile_count,
        "tile_bands": tile_count * len(TILE_BANDS),
        "source_quality": tile_count * len(SOURCES),
        "band_quality": tile_count * len(TILE_BANDS),
    }
    for table, expected in expected_counts.items():
        try:
            actual = int(connection.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        except sqlite3.Error as exc:
            raise CatalogueIntegrityError(
                f"{label} is missing required table {table}; rebuild with scripts/19_build_catalogue.py"
            ) from exc
        if actual != expected:
            raise CatalogueIntegrityError(
                f"{label} table {table} has {actual} rows; expected {expected}"
            )


def _validate_database_snapshot(path: Path, expected_tile_count: int | None = None) -> dict:
    uri = path.resolve().as_uri() + "?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    try:
        user_version = int(connection.execute("PRAGMA user_version").fetchone()[0])
        if user_version != CATALOGUE_SCHEMA_VERSION:
            raise CatalogueIntegrityError(
                f"Staging database schema version {user_version} is not {CATALOGUE_SCHEMA_VERSION}"
            )
        integrity = str(connection.execute("PRAGMA integrity_check").fetchone()[0]).lower()
        if integrity != "ok":
            raise CatalogueIntegrityError(f"Staging database integrity_check failed: {integrity}")
        metadata = _read_authoritative_metadata(connection, "Staging database")
        tile_count = int(metadata["tile_count"])
        if expected_tile_count is not None and tile_count != expected_tile_count:
            raise CatalogueIntegrityError(
                f"Staging database metadata tile count {tile_count} does not match expected {expected_tile_count}"
            )
        _validate_database_counts(connection, metadata, "Staging database")
        return metadata
    finally:
        connection.close()


def _serialize_metadata_export(path: Path, metadata: Mapping) -> None:
    serialized = json.dumps(metadata, indent=2, sort_keys=False, allow_nan=False) + "\n"
    path.write_text(serialized, encoding="utf-8")


def _validate_json_export(path: Path, authoritative: Mapping) -> None:
    exported = _validate_metadata_contract(_json_load(path), "JSON export")
    if exported != dict(authoritative):
        if exported.get("build_id") != authoritative.get("build_id"):
            detail = "build identifiers differ"
        else:
            detail = "metadata objects differ despite matching build identifiers"
        raise CatalogueIntegrityError(
            f"JSON export disagrees with SQLite authoritative metadata: {detail}"
        )


def _replace_published_file(staged_path: Path, published_path: Path) -> None:
    os.replace(staged_path, published_path)


def _publish_staged_catalogue(staged_database: Path, staged_json: Path, output: Path) -> None:
    try:
        _replace_published_file(staged_database, output / CATALOGUE_DB_NAME)
    except OSError as exc:
        raise CataloguePublicationError(
            f"Catalogue database publication failed; the previous published pair was preserved: {exc}"
        ) from exc
    try:
        _replace_published_file(staged_json, output / CATALOGUE_JSON_NAME)
    except OSError as exc:
        raise CataloguePublicationError(
            "Catalogue JSON publication failed after database publication; the directory is an "
            "incomplete generation and readers will refuse it. Rerun an explicit catalogue rebuild."
        ) from exc


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(block)
    except OSError as exc:
        raise CatalogueError(f"Cannot hash input file {path}: {exc}") from exc
    return digest.hexdigest()


def _resolve_existing_file(path: str | Path, label: str) -> Path:
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CatalogueError(f"{label} is not available: {candidate}") from exc
    if not resolved.is_file():
        raise CatalogueError(f"{label} is not a regular file: {resolved}")
    return resolved


def _resolve_directory(path: str | Path, label: str) -> Path:
    candidate = Path(path)
    try:
        resolved = candidate.resolve(strict=True)
    except OSError as exc:
        raise CatalogueError(f"{label} is not available: {candidate}") from exc
    if not resolved.is_dir():
        raise CatalogueError(f"{label} is not a directory: {resolved}")
    return resolved


def _numbers_equal(left, right, *, shape: bool = False) -> bool:
    try:
        if shape:
            return tuple(int(value) for value in left) == tuple(int(value) for value in right)
        return np.allclose(
            tuple(float(value) for value in left),
            tuple(float(value) for value in right),
            rtol=0.0,
            atol=1e-6,
        )
    except (TypeError, ValueError):
        return False


def _tile_from_record(tile_id: str, record: Mapping) -> Tile:
    value = record.get("tile") if isinstance(record.get("tile"), Mapping) else record
    try:
        tile = Tile(
            tile_id=str(value["tile_id"]),
            row=int(value["row"]),
            col=int(value["col"]),
            bbox=tuple(float(item) for item in value["bbox_epsg27700"]),
            source_resolution_m=int(value["source_resolution_m"]),
            output_resolution_m=int(value["output_resolution_m"]),
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise CatalogueError(f"Manifest tile {tile_id} has an invalid tile definition") from exc
    if tile.tile_id != tile_id:
        raise CatalogueError(f"Manifest tile key {tile_id} disagrees with tile_id {tile.tile_id}")
    if len(tile.bbox) != 4 or tile.bbox[2] <= tile.bbox[0] or tile.bbox[3] <= tile.bbox[1]:
        raise CatalogueError(f"Manifest tile {tile_id} has invalid bounds")
    stored = record.get("tile") if isinstance(record.get("tile"), Mapping) else record
    if "source_shape" in stored and not _numbers_equal(stored["source_shape"], tile.source_shape, shape=True):
        raise CatalogueError(f"Manifest tile {tile_id} source dimensions disagree with its definition")
    if "output_shape" in stored and not _numbers_equal(stored["output_shape"], tile.output_shape, shape=True):
        raise CatalogueError(f"Manifest tile {tile_id} output dimensions disagree with its definition")
    return tile


def _manifest_tiles(manifest: Mapping) -> list[tuple[str, Mapping, Tile]]:
    records = manifest.get("tiles")
    if isinstance(records, Mapping):
        raw = [(str(tile_id), record) for tile_id, record in records.items()]
    elif isinstance(records, list):
        raw = []
        for record in records:
            if not isinstance(record, Mapping):
                raise CatalogueError("Manifest tile list contains a non-object record")
            nested = record.get("tile") if isinstance(record.get("tile"), Mapping) else record
            tile_id = nested.get("tile_id") if isinstance(nested, Mapping) else None
            raw.append((str(tile_id), record))
    else:
        raise CatalogueError("Manifest must contain a tiles object or list")
    seen: set[str] = set()
    result = []
    for tile_id, record in raw:
        if tile_id in seen:
            raise CatalogueError(f"Manifest contains duplicate tile ID: {tile_id}")
        seen.add(tile_id)
        if not isinstance(record, Mapping):
            raise CatalogueError(f"Manifest tile {tile_id} record is not an object")
        result.append((tile_id, record, _tile_from_record(tile_id, record)))
    return sorted(result, key=lambda item: item[0])


def _expected_transform(tile: Tile) -> tuple[float, ...]:
    return tuple(Affine(
        tile.output_resolution_m, 0.0, tile.bbox[0],
        0.0, -tile.output_resolution_m, tile.bbox[3],
    ))


def _expected_request_bounds(tile: Tile, source: str, version: str | None) -> tuple[float, ...]:
    minx, miny, maxx, maxy = tile.bbox
    padding = tile.source_resolution_m / 2.0 if source == "airport" and version == "2.0.1" else 0.0
    return (minx - padding, miny - padding, maxx + padding, maxy + padding)


def _raw_grid_from_manifest(record: Mapping, source: str) -> Mapping | None:
    source_info = record.get("source_info", {})
    if not isinstance(source_info, Mapping):
        return None
    info = source_info.get(source, {})
    if not isinstance(info, Mapping):
        return None
    raw = info.get("raw_grid")
    return raw if isinstance(raw, Mapping) else None


def _skip_from_manifest(record: Mapping, source: str) -> bool:
    source_info = record.get("source_info", {})
    if not isinstance(source_info, Mapping):
        return False
    info = source_info.get(source, {})
    return isinstance(info, Mapping) and bool(info.get("skipped_outside_declared_coverage"))


def _report_rows(report: Mapping) -> dict[tuple[str, str], Mapping]:
    rows = report.get("rows")
    if not isinstance(rows, list):
        raise CatalogueError("Reconciliation report must contain a rows list")
    indexed: dict[tuple[str, str], Mapping] = {}
    for row in rows:
        if not isinstance(row, Mapping):
            raise CatalogueError("Reconciliation report contains a non-object row")
        key = (str(row.get("tile_id")), str(row.get("source")))
        if key in indexed:
            raise CatalogueError(f"Reconciliation report contains duplicate/conflicting record: {key}")
        if key[1] not in SOURCES:
            raise CatalogueError(f"Reconciliation report contains unknown source: {key[1]}")
        indexed[key] = row
    return indexed


def _validate_report_against_manifest(
    manifest_records: list[tuple[str, Mapping, Tile]],
    manifest: Mapping,
    report: Mapping,
    *,
    declared_bounds_by_source: Mapping[str, Any] | None = None,
    target_crs: str | None = None,
) -> dict[tuple[str, str], Mapping]:
    rows = _report_rows(report)
    declared_bounds_by_source = declared_bounds_by_source or {}
    target_crs = target_crs or manifest.get("crs", TARGET_CRS)
    expected_keys = {(tile_id, source) for tile_id, _, _ in manifest_records for source in SOURCES}
    unknown = sorted(set(rows) - expected_keys)
    missing = sorted(expected_keys - set(rows))
    if unknown:
        raise CatalogueError(f"Reconciliation report has unknown tile/source records: {unknown[:8]}")
    if missing:
        raise CatalogueError(f"Reconciliation report is missing tile/source records: {missing[:8]}")

    for tile_id, record, tile in manifest_records:
        for source in SOURCES:
            row = rows[(tile_id, source)]
            if str(row.get("policy_version")) != GRID_POLICY_VERSION:
                raise CatalogueError(f"Reconciliation policy version is not supported for {tile_id}/{source}")
            raw = _raw_grid_from_manifest(record, source)
            version = row.get("wcs_version")
            if version is not None and version not in SUPPORTED_WCS_VERSIONS:
                raise CatalogueError(
                    f"Reconciliation WCS version is not supported for {tile_id}/{source}: {version!r}"
                )
            expected_request = _expected_request_bounds(tile, source, version)
            if row.get("requested_shape") is not None and not _numbers_equal(
                row["requested_shape"], tile.source_shape, shape=True
            ):
                raise CatalogueError(f"Reconciliation requested dimensions disagree for {tile_id}/{source}")
            if row.get("requested_bounds_epsg27700") is not None and not _numbers_equal(
                row["requested_bounds_epsg27700"], expected_request
            ):
                raise CatalogueError(f"Reconciliation requested bounds disagree for {tile_id}/{source}")
            response_fields = (
                ("recorded_response_shape", "shape", True),
                ("recorded_response_bounds_epsg27700", "bounds", False),
                ("recorded_response_transform", "transform", False),
            )
            if raw is None:
                if any(row.get(field) is not None for field, _, _ in response_fields):
                    raise CatalogueError(f"Reconciliation records a response without manifest raw_grid for {tile_id}/{source}")
                expected_acceptance = "skipped" if _skip_from_manifest(record, source) else "missing_evidence"
                if row.get("policy_acceptance") != expected_acceptance:
                    raise CatalogueError(f"Reconciliation no-grid state disagrees for {tile_id}/{source}")
                continue
            if not isinstance(version, str) or not version:
                raise CatalogueError(
                    f"Reconciliation omits supported WCS version needed to substantiate {tile_id}/{source}"
                )
            if row.get("requested_shape") is None or row.get("requested_bounds_epsg27700") is None:
                raise CatalogueError(
                    f"Reconciliation omits requested geometry needed to substantiate {tile_id}/{source}"
                )
            for field, raw_key, is_shape in response_fields:
                if row.get(field) is None:
                    raise CatalogueError(f"Reconciliation omits {field} for {tile_id}/{source}")
                if not _numbers_equal(row[field], raw.get(raw_key), shape=is_shape):
                    raise CatalogueError(f"Reconciliation response geometry disagrees for {tile_id}/{source}: {field}")
            reported_acceptance = row.get("policy_acceptance")
            if reported_acceptance not in {"accepted", "rejected"}:
                raise CatalogueError(f"Reconciliation raw-grid state is invalid for {tile_id}/{source}")

            target_grid = tile_grid(tile, target_crs)
            try:
                decision = validate_source_grid(
                    source,
                    tile_id,
                    target_grid,
                    dict(raw),
                    version,
                    declared_bounds=declared_bounds_by_source.get(source),
                )
            except ValueError as exc:
                expected_acceptance = "rejected"
                decision = None
                decision_detail = str(exc)
            else:
                expected_acceptance = "accepted"
                decision_detail = f"shared policy {decision['policy_name']} v{decision['policy_version']}"

            if reported_acceptance != expected_acceptance:
                raise CatalogueIntegrityError(
                    f"Reconciliation source-grid acceptance contradicts shared validator for "
                    f"{tile_id}/{source}: report={reported_acceptance!r}, "
                    f"recomputed={expected_acceptance!r}; {decision_detail}"
                )

            policy_name = row.get("policy_name")
            allowed_policy_names = {EXACT_GRID_POLICY["name"]}
            if source == "airport":
                allowed_policy_names.update({
                    AIRPORT_PADDED_GRID_POLICY["name"],
                    f"{EXACT_GRID_POLICY['name']} or {AIRPORT_PADDED_GRID_POLICY['name']}",
                })
            if policy_name is not None and policy_name not in allowed_policy_names:
                raise CatalogueError(
                    f"Reconciliation policy identifier is not supported for {tile_id}/{source}: {policy_name!r}"
                )
            if expected_acceptance == "accepted":
                if not isinstance(policy_name, str) or policy_name != decision["policy_name"]:
                    raise CatalogueIntegrityError(
                        f"Reconciliation policy identifier contradicts shared validator for "
                        f"{tile_id}/{source}: report={policy_name!r}, "
                        f"recomputed={decision['policy_name']!r}"
                    )
    return rows


def _source_state(row: Mapping) -> str:
    acceptance = row.get("policy_acceptance")
    mapping = {
        "accepted": "grid_accepted",
        "rejected": "grid_rejected",
        "skipped": "outside_declared_coverage",
        "missing_evidence": "missing_evidence",
    }
    try:
        return mapping[acceptance]
    except KeyError as exc:
        raise CatalogueError(f"Unsupported source assessment state: {acceptance!r}") from exc


def _band_qualification(source_rows: Mapping[str, Mapping], band: str) -> dict:
    dependencies = BAND_DEPENDENCIES[band]
    assessments = [source_rows[source] for source in dependencies]
    states = [_source_state(row) for row in assessments]
    if any(state in {"grid_rejected", "missing_evidence"} for state in states):
        qualification = "withheld_due_to_source_quality"
        reason = "; ".join(
            f"{source}: {row.get('evidence_based_classification') or row.get('policy_acceptance')}"
            for source, row, state in zip(dependencies, assessments, states)
            if state in {"grid_rejected", "missing_evidence"}
        )
    elif any(state == "outside_declared_coverage" for state in states):
        qualification = "qualified_with_coverage_limitation"
        reason = "; ".join(
            f"{source}: explicit outside-declared-coverage skip; this is not evidence of silence"
            for source, state in zip(dependencies, states)
            if state == "outside_declared_coverage"
        )
    else:
        qualification = "qualified"
        reason = "All dependent sources have accepted source-grid evidence. This is not independent acoustic validation."
    return {
        "qualification": qualification,
        "dependencies": list(dependencies),
        "reason": reason,
        "provenance": "Derived from the supplied reconciliation report at tile level; source-grid acceptance is not acoustic validation.",
    }


def _reference_years(manifest: Mapping, reference_metadata_path: Path | None) -> dict:
    supplied: Mapping | None = None
    provenance = "not supplied; no source year inferred from coverage labels"
    if reference_metadata_path is not None:
        supplied = _json_load(reference_metadata_path)
        provenance = f"explicit reference metadata: {reference_metadata_path}"
    elif isinstance(manifest.get("reference_years"), Mapping):
        supplied = manifest["reference_years"]
        provenance = "reference_years field in supplied historical manifest"
    values = supplied.get("reference_years", supplied) if isinstance(supplied, Mapping) else {}
    result = {}
    for source in SOURCES:
        value = values.get(source) if isinstance(values, Mapping) else None
        if isinstance(value, Mapping):
            year = value.get("year", value.get("value"))
            source_provenance = value.get("provenance", provenance)
        else:
            year = value
            source_provenance = provenance
        if year is None:
            result[source] = {"value": None, "status": "not_established", "provenance": source_provenance}
            continue
        try:
            year_int = int(year)
        except (TypeError, ValueError) as exc:
            raise CatalogueError(f"Reference year for {source} is invalid: {year!r}") from exc
        if year_int < 1000 or year_int > 9999:
            raise CatalogueError(f"Reference year for {source} is outside the four-digit range: {year_int}")
        result[source] = {"value": year_int, "status": "established", "provenance": source_provenance}
    return result


def _validate_mask_header(mask_path: Path) -> dict:
    with rasterio.open(mask_path) as dataset:
        if dataset.count != 1:
            raise CatalogueError(f"England land mask must have one band, got {dataset.count}")
        if str(dataset.crs) != TARGET_CRS:
            raise CatalogueError(f"England land mask CRS must be {TARGET_CRS}, got {dataset.crs}")
        transform = tuple(dataset.transform)
        if not np.allclose(transform, (100.0, 0.0, transform[2], 0.0, -100.0, transform[5], 0.0, 0.0, 1.0), rtol=0.0, atol=1e-6):
            raise CatalogueError("England land mask must be an unrotated 100 m EPSG:27700 grid")
        return {
            "shape": list(dataset.shape),
            "crs": str(dataset.crs),
            "transform": list(transform),
            "bounds": [float(value) for value in dataset.bounds],
            "dtype": dataset.dtypes[0],
            "nodata": None if dataset.nodata is None else float(dataset.nodata),
        }


def _semantic_config(config: Mapping | None) -> dict:
    configured = {} if config is None else config.get("reporting_threshold_db", {})
    if not isinstance(configured, Mapping):
        raise CatalogueError("Catalogue semantic validation thresholds must be an object")
    thresholds = dict(DEFAULT_REPORTING_THRESHOLDS)
    thresholds.update(dict(configured))
    return {"reporting_threshold_db": thresholds}


def _read_aligned_land_mask(mask_path: Path, tile: Tile) -> np.ndarray:
    expected_height, expected_width = tuple(tile.output_shape)
    with rasterio.open(mask_path) as dataset:
        transform = tuple(dataset.transform)
        x_start = (tile.bbox[0] - transform[2]) / transform[0]
        x_stop = (tile.bbox[2] - transform[2]) / transform[0]
        y_start = (transform[5] - tile.bbox[3]) / abs(transform[4])
        y_stop = (transform[5] - tile.bbox[1]) / abs(transform[4])
        indices = (x_start, x_stop, y_start, y_stop)
        if any(abs(value - round(value)) > 1e-6 for value in indices):
            raise CatalogueIntegrityError(
                f"England land mask grid is not aligned to tile {tile.tile_id}"
            )
        if round(x_stop - x_start) != expected_width or round(y_stop - y_start) != expected_height:
            raise CatalogueIntegrityError(
                f"England land mask dimensions do not align to tile {tile.tile_id}"
            )
    values = read_tile_land_mask(mask_path, tile.bbox, tuple(tile.output_shape))
    if values.shape != (expected_height, expected_width):
        raise CatalogueIntegrityError(
            f"England land mask read shape disagrees with tile {tile.tile_id}: "
            f"expected {(expected_height, expected_width)}, got {values.shape}"
        )
    return values


def _safe_child(root: Path, relative: str, label: str) -> Path:
    candidate = Path(relative)
    if candidate.is_absolute():
        raise CatalogueError(f"{label} must be relative: {relative}")
    try:
        resolved = (root / candidate).resolve(strict=True)
    except OSError as exc:
        raise CatalogueError(f"{label} is missing beneath supplied root: {relative}") from exc
    try:
        resolved.relative_to(root)
    except ValueError as exc:
        raise CatalogueError(f"{label} escapes supplied root: {relative}") from exc
    if not resolved.is_file():
        raise CatalogueError(f"{label} is not a regular file: {resolved}")
    return resolved


def _inventory_tile_files(tile_root: Path, expected_ids: set[str]) -> None:
    present_ids = set()
    unknown = []
    for entry in sorted(tile_root.iterdir(), key=lambda item: item.name):
        match = TILE_FILENAME_PATTERN.fullmatch(entry.name)
        if not match:
            continue
        tile_id = match.group(1)
        if tile_id in present_ids:
            raise CatalogueError(f"Tile directory contains duplicate tile ID: {tile_id}")
        present_ids.add(tile_id)
        if tile_id not in expected_ids:
            unknown.append(entry.name)
    missing = sorted(expected_ids - present_ids)
    if missing or unknown:
        details = []
        if missing:
            details.append(f"missing={missing[:8]}")
        if unknown:
            details.append(f"unexpected={unknown[:8]}")
        raise CatalogueError("Tile directory inventory mismatch: " + "; ".join(details))


def _inspect_tile(
    tile_root: Path,
    tile: Tile,
    mask_path: Path,
    semantic_config: Mapping,
) -> dict:
    path = _safe_child(tile_root, f"{tile.tile_id}.tif", f"Tile {tile.tile_id}")
    before = path.stat()
    expected_shape = tuple(tile.output_shape)
    expected_transform = _expected_transform(tile)
    with rasterio.open(path) as dataset:
        crs = str(dataset.crs) if dataset.crs is not None else None
        if crs != TARGET_CRS:
            raise CatalogueError(f"Tile {tile.tile_id} CRS mismatch: expected {TARGET_CRS}, got {crs}")
        if dataset.shape != expected_shape:
            raise CatalogueError(f"Tile {tile.tile_id} dimensions mismatch: expected {expected_shape}, got {dataset.shape}")
        if not np.allclose(tuple(dataset.transform), expected_transform, rtol=0.0, atol=1e-6):
            raise CatalogueError(f"Tile {tile.tile_id} transform mismatch")
        expected_bounds = (tile.bbox[0], tile.bbox[1], tile.bbox[2], tile.bbox[3])
        if not np.allclose(tuple(dataset.bounds), expected_bounds, rtol=0.0, atol=1e-6):
            raise CatalogueError(f"Tile {tile.tile_id} bounds mismatch")
        descriptions = list(dataset.descriptions)
        if dataset.count != len(TILE_BANDS) or descriptions != list(TILE_BANDS):
            raise CatalogueError(f"Tile {tile.tile_id} band schema mismatch: got {descriptions}")
        dtypes = list(dataset.dtypes)
        if dtypes != ["float32"] * len(TILE_BANDS):
            raise CatalogueError(f"Tile {tile.tile_id} dtype schema mismatch: got {dtypes}")
        nodata_values = [None if value is None else float(value) for value in dataset.nodatavals]
        if any(value is None or not math.isclose(value, -9999.0, rel_tol=0.0, abs_tol=0.0) for value in nodata_values):
            raise CatalogueError(f"Tile {tile.tile_id} nodata schema mismatch: got {nodata_values}")
        arrays = dataset.read(indexes=list(range(1, len(TILE_BANDS) + 1)), masked=False)
    land = _read_aligned_land_mask(mask_path, tile)
    try:
        validate_production_arrays(
            arrays,
            land,
            nodata=DEFAULT_NODATA,
            band_names=TILE_BANDS,
            config=semantic_config,
        )
    except (TypeError, ValueError) as exc:
        raise CatalogueIntegrityError(
            f"Tile {tile.tile_id} semantic integrity validation failed: {exc}"
        ) from exc
    after = path.stat()
    if before.st_size != after.st_size or before.st_mtime_ns != after.st_mtime_ns:
        raise CatalogueError(f"Tile {tile.tile_id} changed while being catalogued")
    return {
        "path": path,
        "filename": f"{tile.tile_id}.tif",
        "bounds": tile.bbox,
        "shape": expected_shape,
        "transform": expected_transform,
        "crs": TARGET_CRS,
        "bands": [
            {"index": index, "name": name, "dtype": "float32", "nodata": -9999.0}
            for index, name in enumerate(TILE_BANDS, start=1)
        ],
        "file_size": int(after.st_size),
        "sha256": _sha256_file(path),
    }


def _prepare_output(output_dir: Path, overwrite: bool) -> None:
    if output_dir.exists() and not output_dir.is_dir():
        raise CatalogueError(f"Catalogue output path is not a directory: {output_dir}")
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"Catalogue output directory is not empty; pass --overwrite: {output_dir}")
    output_dir.mkdir(parents=True, exist_ok=True)
    if not overwrite:
        for name in (CATALOGUE_JSON_NAME, CATALOGUE_DB_NAME):
            if (output_dir / name).exists():
                raise FileExistsError(f"Catalogue output already exists: {output_dir / name}")


def _create_database(path: Path) -> sqlite3.Connection:
    if path.exists():
        raise CatalogueError(f"Staging database path already exists: {path}")
    connection = sqlite3.connect(path)
    connection.execute("PRAGMA journal_mode = DELETE")
    connection.execute("PRAGMA synchronous = FULL")
    connection.executescript(
        f"""
        PRAGMA user_version = {CATALOGUE_SCHEMA_VERSION};
        CREATE TABLE catalogue_info (
            key TEXT PRIMARY KEY,
            value_json TEXT NOT NULL
        );
        CREATE TABLE tiles (
            tile_id TEXT PRIMARY KEY,
            row_index INTEGER NOT NULL,
            col_index INTEGER NOT NULL,
            filename TEXT NOT NULL UNIQUE,
            minx REAL NOT NULL,
            miny REAL NOT NULL,
            maxx REAL NOT NULL,
            maxy REAL NOT NULL,
            width INTEGER NOT NULL,
            height INTEGER NOT NULL,
            crs TEXT NOT NULL,
            transform_json TEXT NOT NULL,
            file_size INTEGER NOT NULL,
            sha256 TEXT NOT NULL,
            UNIQUE(row_index, col_index)
        );
        CREATE INDEX idx_tiles_bounds ON tiles(minx, maxx, miny, maxy);
        CREATE TABLE tile_bands (
            tile_id TEXT NOT NULL REFERENCES tiles(tile_id),
            band_index INTEGER NOT NULL,
            name TEXT NOT NULL,
            dtype TEXT NOT NULL,
            nodata REAL NOT NULL,
            PRIMARY KEY(tile_id, band_index),
            UNIQUE(tile_id, name)
        );
        CREATE TABLE source_quality (
            tile_id TEXT NOT NULL REFERENCES tiles(tile_id),
            source TEXT NOT NULL,
            state TEXT NOT NULL,
            policy_acceptance TEXT NOT NULL,
            policy_name TEXT,
            rejection_category TEXT,
            classification TEXT,
            disposition TEXT,
            coverage_relationship TEXT,
            geometric_pattern TEXT,
            reason TEXT NOT NULL,
            provenance TEXT,
            assessment_json TEXT NOT NULL,
            PRIMARY KEY(tile_id, source)
        );
        CREATE TABLE band_quality (
            tile_id TEXT NOT NULL REFERENCES tiles(tile_id),
            band_name TEXT NOT NULL,
            qualification TEXT NOT NULL,
            dependencies_json TEXT NOT NULL,
            reason TEXT NOT NULL,
            provenance TEXT NOT NULL,
            PRIMARY KEY(tile_id, band_name)
        );
        """
    )
    return connection


def _build_catalogue_unlocked(
    tile_dir: str | Path,
    manifest_path: str | Path,
    reconciliation_report_path: str | Path,
    land_mask_path: str | Path,
    output_dir: str | Path,
    *,
    dataset_label: str = "Quiet UK England historical four-band dataset",
    reference_metadata_path: str | Path | None = None,
    config_path: str | Path | None = None,
    overwrite: bool = False,
) -> dict:
    """Build a validated SQLite/JSON catalogue without changing source data."""
    tile_root = _resolve_directory(tile_dir, "Tile directory")
    manifest_file = _resolve_existing_file(manifest_path, "Historical manifest")
    report_file = _resolve_existing_file(reconciliation_report_path, "Reconciliation report")
    mask_file = _resolve_existing_file(land_mask_path, "England land mask")
    reference_file = None if reference_metadata_path is None else _resolve_existing_file(reference_metadata_path, "Reference metadata")
    config_file = None if config_path is None else _resolve_existing_file(config_path, "Production configuration")
    output = Path(output_dir).resolve()
    _prepare_output(output, overwrite)

    manifest = _json_load(manifest_file)
    report = _json_load(report_file)
    config_payload = None if config_file is None else _json_load(config_file)
    declared_bounds_by_source = {} if config_payload is None else config_payload.get("coverage_bounds_epsg27700", {})
    if not isinstance(declared_bounds_by_source, Mapping):
        raise CatalogueError("Production configuration coverage_bounds_epsg27700 must be an object")
    semantic_config = _semantic_config(config_payload)
    target_crs = (
        config_payload.get("crs") if isinstance(config_payload, Mapping) else None
    ) or manifest.get("crs", TARGET_CRS)
    manifest_records = _manifest_tiles(manifest)
    report_rows = _validate_report_against_manifest(
        manifest_records,
        manifest,
        report,
        declared_bounds_by_source=declared_bounds_by_source,
        target_crs=target_crs,
    )
    _inventory_tile_files(tile_root, {tile_id for tile_id, _, _ in manifest_records})
    mask_header = _validate_mask_header(mask_file)

    tile_headers = {}
    source_counts = {source: Counter() for source in SOURCES}
    band_counts = {band: Counter() for band in TILE_BANDS}
    source_rows = {}
    band_rows = {}
    for tile_id, record, tile in manifest_records:
        tile_headers[tile_id] = _inspect_tile(tile_root, tile, mask_file, semantic_config)
        per_source = {}
        for source in SOURCES:
            row = report_rows[(tile_id, source)]
            state = _source_state(row)
            per_source[source] = row
            source_counts[source][state] += 1
        source_rows[tile_id] = per_source
        per_band = {}
        for band in TILE_BANDS:
            assessment = _band_qualification(per_source, band)
            per_band[band] = assessment
            band_counts[band][assessment["qualification"]] += 1
        band_rows[tile_id] = per_band

    input_metadata = {
        "tile_directory": {
            "path": str(tile_root),
            "provenance": "explicit tile directory supplied to catalogue builder; manifest output paths were not used",
        },
        "manifest": {"path": str(manifest_file), "sha256": _sha256_file(manifest_file)},
        "reconciliation_report": {
            "path": str(report_file),
            "sha256": _sha256_file(report_file),
            "provenance": "hash computed during this build; older report provenance may not include its own input hashes",
        },
        "land_mask": {
            "path": str(mask_file),
            "sha256": _sha256_file(mask_file),
            "file_size": int(mask_file.stat().st_size),
            "header": mask_header,
        },
    }
    if config_file is not None:
        input_metadata["production_config"] = {
            "path": str(config_file),
            "sha256": _sha256_file(config_file),
            "provenance": "optional configuration supplied to recompute source-grid domain context and frozen semantic thresholds",
        }
    if reference_file is not None:
        input_metadata["reference_metadata"] = {
            "path": str(reference_file),
            "sha256": _sha256_file(reference_file),
        }
    metadata = {
        "catalogue_schema_version": CATALOGUE_SCHEMA_VERSION,
        "catalogue_validation_contract_version": CATALOGUE_VALIDATION_CONTRACT_VERSION,
        "catalogue_publication_contract_version": CATALOGUE_PUBLICATION_CONTRACT_VERSION,
        "build_id": uuid.uuid4().hex,
        "dataset_label": str(dataset_label),
        "build_timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "grid": {"crs": TARGET_CRS, "resolution_m": 100, "tile_resolution_m": 10},
        "band_dictionary": BAND_DICTIONARY,
        "semantic_validation": {
            "contract_version": CATALOGUE_VALIDATION_CONTRACT_VERSION,
            "nodata": float(DEFAULT_NODATA),
            "reporting_threshold_db": semantic_config["reporting_threshold_db"],
        },
        "inputs": input_metadata,
        "tile_count": len(manifest_records),
        "quality_counts": {
            "source_states": {source: dict(sorted(counter.items())) for source, counter in source_counts.items()},
            "band_qualifications": {band: dict(sorted(counter.items())) for band, counter in band_counts.items()},
        },
        "reference_years": _reference_years(manifest, reference_file),
        "limitations": [
            "This catalogue exposes a historical England raster baseline; it is not a public release or completed mapping product.",
            "Source-grid acceptance is structural evidence and does not independently validate acoustic accuracy or provider semantics.",
            "Band withholding is conservative at tile level: it does not prove every cell in a tile is wrong.",
            "The frozen four-band product has no airport-inclusive combined_upper_db band.",
            "The road_rail_upper_db band is not an upper bound for the airport-inclusive combined_reported_lower_db band.",
            "Reference years are reported only where explicitly supplied; absent years are not inferred from coverage names.",
            "The historical source-grid reconciliation report may lack original input hashes; this catalogue records newly computed hashes without upgrading that provenance.",
            "This v2 catalogue validation contract independently recomputes source-grid acceptance and validates frozen four-band semantics; v1 catalogues must be rebuilt.",
        ],
    }

    staging_dir = Path(tempfile.mkdtemp(prefix=".catalogue-build-", dir=output))
    staging_database = staging_dir / CATALOGUE_DB_NAME
    staging_json = staging_dir / CATALOGUE_JSON_NAME
    authoritative_metadata = None
    try:
        connection = None
        try:
            connection = _create_database(staging_database)
            connection.execute(
                "INSERT INTO catalogue_info(key, value_json) VALUES (?, ?)",
                ("metadata", json.dumps(metadata, sort_keys=True, allow_nan=False)),
            )
            for tile_id, record, tile in manifest_records:
                header = tile_headers[tile_id]
                connection.execute(
                    """INSERT INTO tiles(
                        tile_id,row_index,col_index,filename,minx,miny,maxx,maxy,width,height,crs,
                        transform_json,file_size,sha256
                    ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                    (
                        tile_id, tile.row, tile.col, header["filename"], tile.bbox[0], tile.bbox[1], tile.bbox[2], tile.bbox[3],
                        tile.output_shape[1], tile.output_shape[0], header["crs"], json.dumps(list(header["transform"])),
                        header["file_size"], header["sha256"],
                    ),
                )
                for band in header["bands"]:
                    connection.execute(
                        "INSERT INTO tile_bands(tile_id,band_index,name,dtype,nodata) VALUES(?,?,?,?,?)",
                        (tile_id, band["index"], band["name"], band["dtype"], band["nodata"]),
                    )
                for source in SOURCES:
                    row = source_rows[tile_id][source]
                    state = _source_state(row)
                    reason = row.get("evidence_based_classification") or row.get("policy_acceptance") or "No reason recorded"
                    connection.execute(
                        """INSERT INTO source_quality(
                            tile_id,source,state,policy_acceptance,policy_name,rejection_category,
                            classification,disposition,coverage_relationship,geometric_pattern,
                            reason,provenance,assessment_json
                        ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                        (
                            tile_id, source, state, row.get("policy_acceptance"), row.get("policy_name"),
                            row.get("rejection_category"), row.get("evidence_based_classification"),
                            row.get("proposed_disposition"), row.get("coverage_relationship"), row.get("geometric_pattern"),
                            reason, row.get("metadata_provenance"), json.dumps(dict(row), sort_keys=True, allow_nan=False),
                        ),
                    )
                for band, assessment in band_rows[tile_id].items():
                    connection.execute(
                        """INSERT INTO band_quality(
                            tile_id,band_name,qualification,dependencies_json,reason,provenance
                        ) VALUES(?,?,?,?,?,?)""",
                        (
                            tile_id, band, assessment["qualification"], json.dumps(assessment["dependencies"]),
                            assessment["reason"], assessment["provenance"],
                        ),
                    )
            connection.commit()
        finally:
            if connection is not None:
                connection.close()

        for suffix in ("-wal", "-shm", "-journal"):
            sidecar = Path(str(staging_database) + suffix)
            if sidecar.exists():
                raise CatalogueIntegrityError(
                    f"Staging database still has SQLite sidecar state: {sidecar.name}"
                )
        authoritative_metadata = _validate_database_snapshot(
            staging_database,
            expected_tile_count=len(manifest_records),
        )
        if authoritative_metadata != metadata:
            raise CatalogueIntegrityError(
                "Staging database authoritative metadata does not match the expected build metadata"
            )
        _serialize_metadata_export(staging_json, authoritative_metadata)
        _validate_json_export(staging_json, authoritative_metadata)
        _publish_staged_catalogue(staging_database, staging_json, output)
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)

    database_path = output / CATALOGUE_DB_NAME
    metadata_path = output / CATALOGUE_JSON_NAME
    return {
        "catalogue_dir": str(output),
        "metadata": str(metadata_path),
        "database": str(database_path),
        "metadata_payload": authoritative_metadata,
    }


def build_catalogue(
    tile_dir: str | Path,
    manifest_path: str | Path,
    reconciliation_report_path: str | Path,
    land_mask_path: str | Path,
    output_dir: str | Path,
    *,
    dataset_label: str = "Quiet UK England historical four-band dataset",
    reference_metadata_path: str | Path | None = None,
    config_path: str | Path | None = None,
    overwrite: bool = False,
) -> dict:
    """Build and publish a validated catalogue under a nonblocking output lock."""
    output = Path(output_dir).resolve()
    try:
        with resource_lock(output, "catalogue output"):
            return _build_catalogue_unlocked(
                tile_dir,
                manifest_path,
                reconciliation_report_path,
                land_mask_path,
                output,
                dataset_label=dataset_label,
                reference_metadata_path=reference_metadata_path,
                config_path=config_path,
                overwrite=overwrite,
            )
    except ResourceLockError as exc:
        raise CatalogueError(
            f"Catalogue output is locked or unavailable: {output}; another build may be publishing"
        ) from exc


def _finite_coordinate(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise CatalogueError(f"{label} must be numeric") from exc
    if not math.isfinite(result):
        raise CatalogueError(f"{label} must be finite")
    return result


def _coordinate_pair(
    x: Any,
    y: Any,
    coordinate_crs: str,
    *,
    easting: Any = None,
    northing: Any = None,
    longitude: Any = None,
    latitude: Any = None,
) -> tuple[float, float, str, dict]:
    crs = str(coordinate_crs).upper()
    if any(value is not None for value in (easting, northing, longitude, latitude)):
        if x is not None or y is not None:
            raise CatalogueError("Provide either positional coordinates or named coordinates, not both")
        if crs == TARGET_CRS:
            if easting is None or northing is None or longitude is not None or latitude is not None:
                raise CatalogueError("BNG lookup requires easting and northing only")
            x, y = easting, northing
        elif crs == WGS84_CRS:
            if longitude is None or latitude is None or easting is not None or northing is not None:
                raise CatalogueError("WGS84 lookup requires longitude and latitude only")
            x, y = longitude, latitude
        else:
            raise CatalogueError(f"Unsupported coordinate CRS: {coordinate_crs!r}")
    if crs not in {TARGET_CRS, WGS84_CRS}:
        raise CatalogueError(f"Coordinate CRS must be {TARGET_CRS} or {WGS84_CRS}, got {coordinate_crs!r}")
    if x is None or y is None:
        raise CatalogueError("A coordinate pair is required")
    first_label, second_label = (
        ("easting", "northing") if crs == TARGET_CRS else ("longitude", "latitude")
    )
    first = _finite_coordinate(x, first_label)
    second = _finite_coordinate(y, second_label)
    if crs == WGS84_CRS and not (-180.0 <= first <= 180.0 and -90.0 <= second <= 90.0):
        raise CatalogueError("WGS84 longitude must be within [-180, 180] and latitude within [-90, 90]")
    requested = {
        "crs": crs,
        first_label: first,
        second_label: second,
    }
    if crs == TARGET_CRS:
        east, north = first, second
    else:
        east_values, north_values = transform_coords(WGS84_CRS, TARGET_CRS, [first], [second])
        east, north = float(east_values[0]), float(north_values[0])
    if not math.isfinite(east) or not math.isfinite(north):
        raise CatalogueError("Coordinate transformation produced non-finite BNG coordinates")
    return east, north, crs, requested


def _json_value(value: Any):
    if value is None:
        return None
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating,)):
        value = float(value)
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, Mapping):
        return {str(key): _json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    return value


class DatasetCatalogue:
    """Read-only SQLite catalogue and session-scoped tile verifier."""

    def __init__(
        self,
        catalogue_dir: str | Path,
        tile_root: str | Path,
        land_mask_path: str | Path | None = None,
    ):
        self.catalogue_dir = _resolve_directory(catalogue_dir, "Catalogue directory")
        self.tile_root = _resolve_directory(tile_root, "Configured tile directory")
        self.metadata_path = self.catalogue_dir / CATALOGUE_JSON_NAME
        self.database_path = self.catalogue_dir / CATALOGUE_DB_NAME
        if not self.database_path.is_file():
            raise CatalogueError(f"Catalogue database is missing: {self.database_path}")
        self.connection = None
        self._verified_tiles: dict[str, tuple[int, int, int | None, str]] = {}
        self._verified_mask: dict[str, tuple[int, int, int | None, str]] = {}
        try:
            uri = self.database_path.as_uri() + "?mode=ro"
            self.connection = sqlite3.connect(uri, uri=True)
            self.connection.row_factory = sqlite3.Row
            self.connection.execute("PRAGMA query_only = ON")
            database_version = int(self.connection.execute("PRAGMA user_version").fetchone()[0])
            if database_version != CATALOGUE_SCHEMA_VERSION:
                raise CatalogueError(
                    f"Catalogue database schema version {database_version!r} is unsupported; "
                    "rebuild it with scripts/19_build_catalogue.py"
                )
            self.metadata = _read_authoritative_metadata(self.connection, "Catalogue")
            _validate_database_counts(self.connection, self.metadata, "Catalogue database")
            _validate_json_export(self.metadata_path, self.metadata)
            self.semantic_config = _semantic_config(self.metadata["semantic_validation"])
            mask_value = land_mask_path
            if mask_value is None:
                mask_value = self.metadata.get("inputs", {}).get("land_mask", {}).get("path")
            self.land_mask_path = _resolve_existing_file(mask_value, "Configured England land mask") if mask_value else None
        except Exception:
            self.close()
            raise

    def close(self) -> None:
        if getattr(self, "connection", None) is not None:
            self.connection.close()
            self.connection = None

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc_value, traceback):
        self.close()

    def _verify_file(self, path: Path, expected_size: int, expected_sha256: str, label: str, cache: dict):
        stat = path.stat()
        identity = (int(stat.st_size), int(stat.st_mtime_ns), getattr(stat, "st_ino", None), str(path))
        previous = cache.get(str(path))
        if previous == identity:
            return
        if stat.st_size != expected_size:
            raise CatalogueIntegrityError(f"{label} size changed: expected {expected_size}, got {stat.st_size}")
        actual = _sha256_file(path)
        after = path.stat()
        after_identity = (int(after.st_size), int(after.st_mtime_ns), getattr(after, "st_ino", None), str(path))
        if identity != after_identity:
            raise CatalogueIntegrityError(f"{label} changed while being verified")
        if actual != expected_sha256:
            raise CatalogueIntegrityError(f"{label} checksum mismatch: expected {expected_sha256}, got {actual}")
        cache[str(path)] = identity

    def _verify_mask(self) -> None:
        if self.land_mask_path is None:
            raise CatalogueIntegrityError("No authoritative England land mask was configured for lookup")
        expected = self.metadata.get("inputs", {}).get("land_mask", {})
        try:
            expected_size = int(expected["file_size"])
            expected_sha = str(expected["sha256"])
        except (OSError, KeyError, TypeError, ValueError) as exc:
            raise CatalogueIntegrityError("Catalogue has incomplete land-mask integrity metadata") from exc
        self._verify_file(self.land_mask_path, expected_size, expected_sha, "England land mask", self._verified_mask)

    def _verify_tile(self, row: sqlite3.Row) -> Path:
        path = _safe_child(self.tile_root, str(row["filename"]), f"Tile {row['tile_id']}")
        self._verify_file(path, int(row["file_size"]), str(row["sha256"]), f"Tile {row['tile_id']}", self._verified_tiles)
        return path

    def iter_tile_records(self):
        """Yield tile rows with their read-only source and band quality rows."""
        if self.connection is None:
            raise CatalogueError("Catalogue connection is closed")
        source_by_tile: dict[str, list[sqlite3.Row]] = {}
        for row in self.connection.execute(
            "SELECT * FROM source_quality ORDER BY tile_id, source"
        ).fetchall():
            source_by_tile.setdefault(str(row["tile_id"]), []).append(row)
        band_by_tile: dict[str, list[sqlite3.Row]] = {}
        for row in self.connection.execute(
            "SELECT * FROM band_quality ORDER BY tile_id, band_name"
        ).fetchall():
            band_by_tile.setdefault(str(row["tile_id"]), []).append(row)
        for tile_row in self.connection.execute(
            "SELECT * FROM tiles ORDER BY row_index, col_index, tile_id"
        ).fetchall():
            tile_id = str(tile_row["tile_id"])
            source_rows = tuple(source_by_tile.get(tile_id, ()))
            band_rows = tuple(band_by_tile.get(tile_id, ()))
            if {str(row["source"]) for row in source_rows} != set(SOURCES):
                raise CatalogueIntegrityError(f"Catalogue source-quality rows are incomplete for tile {tile_id}")
            if {str(row["band_name"]) for row in band_rows} != set(TILE_BANDS):
                raise CatalogueIntegrityError(f"Catalogue band-quality rows are incomplete for tile {tile_id}")
            yield tile_row, source_rows, band_rows

    def verified_tile_path(self, row: sqlite3.Row) -> Path:
        """Verify one tile against its catalogue identity and return its path."""
        return self._verify_tile(row)

    def _base_result(self, requested: dict, east: float, north: float, crs: str) -> dict:
        return {
            "requested_coordinates": requested,
            "transformed_bng": {"crs": TARGET_CRS, "easting_m": east, "northing_m": north},
            "catalogue": {
                "schema_version": self.metadata.get("catalogue_schema_version"),
                "dataset_label": self.metadata.get("dataset_label"),
                "build_timestamp_utc": self.metadata.get("build_timestamp_utc"),
            },
            "reference_years": self.metadata.get("reference_years", {}),
            "coordinate_order": "easting,northing" if crs == TARGET_CRS else "longitude,latitude",
            "tile_id": None,
            "cell": None,
            "land_status": "not_evaluated",
            "source_quality": [],
            "coverage_limitations": [],
            "bands": {},
            "diagnostic": False,
        }

    def _empty_band_results(self, status: str, reason: str) -> dict:
        return {
            band: {
                "value": None,
                "value_status": status,
                "qualification": "not_available",
                "reason": reason,
                "dependencies": list(BAND_DEPENDENCIES[band]),
            }
            for band in TILE_BANDS
        }

    def _land_cell(self, east: float, north: float) -> bool:
        self._verify_mask()
        assert self.land_mask_path is not None
        with rasterio.open(self.land_mask_path) as dataset:
            minx, miny, maxx, maxy = dataset.bounds
            if not (minx <= east < maxx and miny < north <= maxy):
                raise CatalogueIntegrityError("Configured land mask does not cover the selected dataset cell")
            row, col = dataset.index(east, north)
            if not (0 <= row < dataset.height and 0 <= col < dataset.width):
                raise CatalogueIntegrityError("Configured land mask indexing is outside its raster extent")
            value = dataset.read(1, window=Window(col, row, 1, 1))
            return bool(value[0, 0])

    def lookup(
        self,
        x: Any = None,
        y: Any = None,
        coordinate_crs: str = TARGET_CRS,
        *,
        easting: Any = None,
        northing: Any = None,
        longitude: Any = None,
        latitude: Any = None,
        diagnostic: bool = False,
    ) -> dict:
        east, north, crs, requested = _coordinate_pair(
            x, y, coordinate_crs,
            easting=easting, northing=northing, longitude=longitude, latitude=latitude,
        )
        result = self._base_result(requested, east, north, crs)
        result["diagnostic"] = bool(diagnostic)
        candidates = self.connection.execute(
            """SELECT * FROM tiles
               WHERE minx <= ? AND maxx > ? AND miny < ? AND maxy >= ?
               ORDER BY row_index, col_index""",
            (east, east, north, north),
        ).fetchall()
        if not candidates:
            result["land_status"] = "no_dataset_coverage"
            result["bands"] = self._empty_band_results(
                "no_dataset_coverage",
                "No catalogue tile owns this coordinate under west-inclusive/east-exclusive, "
                "south-exclusive/north-inclusive ownership",
            )
            return _json_value(result)
        if len(candidates) != 1:
            raise CatalogueIntegrityError(f"Coordinate has ambiguous tile ownership: {[row['tile_id'] for row in candidates]}")
        tile_row = candidates[0]
        path = self._verify_tile(tile_row)
        result["tile_id"] = tile_row["tile_id"]
        source_rows = self.connection.execute(
            """SELECT * FROM source_quality WHERE tile_id = ?
               ORDER BY CASE source WHEN 'road' THEN 1 WHEN 'rail' THEN 2 WHEN 'airport' THEN 3 END""",
            (tile_row["tile_id"],),
        ).fetchall()
        if len(source_rows) != len(SOURCES):
            raise CatalogueIntegrityError(f"Catalogue source-quality rows are incomplete for tile {tile_row['tile_id']}")
        result["source_quality"] = [
            {
                "source": row["source"],
                "state": row["state"],
                "policy_acceptance": row["policy_acceptance"],
                "classification": row["classification"],
                "reason": row["reason"],
                "disposition": row["disposition"],
                "coverage_relationship": row["coverage_relationship"],
                "geometric_pattern": row["geometric_pattern"],
            }
            for row in source_rows
        ]
        for row in source_rows:
            if row["state"] in {"grid_rejected", "missing_evidence", "outside_declared_coverage"}:
                result["coverage_limitations"].append({
                    "source": row["source"], "state": row["state"], "reason": row["reason"],
                })

        if not self._land_cell(east, north):
            result["land_status"] = "outside_england_land"
            result["bands"] = self._empty_band_results("outside_england_land", "Authoritative England land mask excludes this cell")
            result["coverage_limitations"].append({"reason": "No acoustic value is returned outside England land"})
            return _json_value(result)
        result["land_status"] = "england_land"

        transform = tuple(json.loads(tile_row["transform_json"]))
        height, width = int(tile_row["height"]), int(tile_row["width"])
        col = math.floor((east - transform[2]) / transform[0])
        row_index = math.floor((transform[5] - north) / abs(transform[4]))
        if not (0 <= row_index < height and 0 <= col < width):
            raise CatalogueIntegrityError(f"Coordinate indexing disagrees with tile bounds for {tile_row['tile_id']}")
        minx = transform[2] + col * transform[0]
        maxx = transform[2] + (col + 1) * transform[0]
        maxy = transform[5] + row_index * transform[4]
        miny = transform[5] + (row_index + 1) * transform[4]
        result["cell"] = {
            "row": int(row_index),
            "column": int(col),
            "center_bng": {"easting_m": float((minx + maxx) / 2.0), "northing_m": float((miny + maxy) / 2.0)},
            "bounds_bng": [float(minx), float(miny), float(maxx), float(maxy)],
        }

        band_rows = self.connection.execute(
            "SELECT * FROM band_quality WHERE tile_id = ? ORDER BY band_name", (tile_row["tile_id"],)
        ).fetchall()
        if len(band_rows) != len(TILE_BANDS):
            raise CatalogueIntegrityError(f"Catalogue band-quality rows are incomplete for tile {tile_row['tile_id']}")
        with rasterio.open(path) as dataset:
            values = dataset.read(indexes=list(range(1, len(TILE_BANDS) + 1)), window=Window(col, row_index, 1, 1))[:, 0, 0]
        try:
            validate_production_arrays(
                np.asarray(values, dtype="float64").reshape(len(TILE_BANDS), 1, 1),
                np.ones((1, 1), dtype=bool),
                nodata=DEFAULT_NODATA,
                band_names=TILE_BANDS,
                config=self.semantic_config,
            )
        except (TypeError, ValueError) as exc:
            raise CatalogueIntegrityError(
                f"Tile {tile_row['tile_id']} cell ({row_index},{col}) semantic integrity validation failed: {exc}"
            ) from exc
        by_name = {row["band_name"]: row for row in band_rows}
        for band in TILE_BANDS:
            quality = by_name[band]
            qualification = quality["qualification"]
            dependencies = json.loads(quality["dependencies_json"])
            value = float(values[TILE_BANDS.index(band)])
            is_nodata = not math.isfinite(value) or math.isclose(value, -9999.0, rel_tol=0.0, abs_tol=0.0)
            item = {
                "value": None,
                "value_status": None,
                "qualification": qualification,
                "reason": quality["reason"],
                "dependencies": dependencies,
            }
            if qualification == "withheld_due_to_source_quality" and not diagnostic:
                item["value_status"] = "withheld_due_to_source_quality"
                item["value"] = None
            elif diagnostic:
                lower_censored = band in LOWER_BOUND_BANDS and is_nodata
                item["value"] = None if lower_censored else value
                item["value_status"] = "diagnostic_nodata" if lower_censored else "diagnostic_unqualified"
                item["warning"] = "Raw stored value exposed by explicit diagnostic mode; it is not qualified for ordinary use."
            elif band in LOWER_BOUND_BANDS and is_nodata:
                item["value_status"] = "legitimate_censored_or_unreported_lower_bound"
                item["reason"] = "Stored lower bound is nodata, representing censored/unreported energy."
                item["value"] = None
            else:
                item["value"] = value
                if band == "airport_reported_fraction" and math.isclose(value, 0.0, rel_tol=0.0, abs_tol=0.0):
                    item["value_status"] = "zero_reported_airport_pixels_not_silence"
                    item["reason"] = "Zero reported airport fraction means no reported airport pixels, not silence."
                else:
                    item["value_status"] = "reported_stored_value"
            item["provenance"] = "Stored 100 m cell from the supplied tile; not interpolated."
            result["bands"][band] = item
        return _json_value(result)


def lookup_location(
    catalogue_dir: str | Path,
    tile_root: str | Path,
    x: Any = None,
    y: Any = None,
    coordinate_crs: str = TARGET_CRS,
    *,
    easting: Any = None,
    northing: Any = None,
    longitude: Any = None,
    latitude: Any = None,
    land_mask_path: str | Path | None = None,
    diagnostic: bool = False,
) -> dict:
    """Look up one stored 100 m cell using BNG or WGS84 coordinates."""
    with DatasetCatalogue(catalogue_dir, tile_root, land_mask_path) as catalogue:
        return catalogue.lookup(
            x, y, coordinate_crs,
            easting=easting, northing=northing, longitude=longitude, latitude=latitude,
            diagnostic=diagnostic,
        )
