from __future__ import annotations

import copy
import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import rasterio

from .build_identity import (
    BUILD_IDENTITY_SCHEMA_VERSION,
    compute_build_identity,
    fingerprint_payload,
    identity_differences,
)
from .config import normalize_production_config
from .land_mask import tile_intersects_land
from .locking import acquire_resource_locks, canonical_resource_path
from .tiling import Tile, make_tiles, process_tile, validate_tile_output
from .validation import expected_band_schema


STORAGE_SCHEMA_VERSION = 1
_MANIFEST_LOCK = threading.Lock()
_ERROR_LOG_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json_write(path: Path, payload: dict) -> None:
    with _MANIFEST_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(
            f".{path.name}.{os.getpid()}.{threading.get_ident()}.tmp"
        )
        try:
            temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
            os.replace(temporary, path)
        finally:
            if temporary.exists():
                temporary.unlink()


def _canonical_path(path: str | Path) -> Path:
    return Path(canonical_resource_path(path))


class _ManifestState:
    """Own the mutable manifest and publish coherent snapshots under one lock."""

    def __init__(self, manifest: dict, path: Path):
        self._manifest = manifest
        self.path = path
        self.lock = threading.RLock()
        self._running_ids: set[str] = set()

    def _update(self, mutation):
        with self.lock:
            working = copy.deepcopy(self._manifest)
            result = mutation(working)
            _atomic_json_write(self.path, working)
            self._manifest = working
            return result

    def publish(self) -> None:
        self._update(lambda manifest: None)

    def update_record(self, tile_id: str, mutation):
        def update(manifest):
            return mutation(manifest["tiles"][tile_id])

        return self._update(update)

    def update_manifest(self, mutation):
        return self._update(mutation)

    def record_snapshot(self, tile_id: str) -> dict:
        with self.lock:
            return copy.deepcopy(self._manifest["tiles"][tile_id])

    def add_running(self, tile_id: str) -> None:
        with self.lock:
            self._running_ids.add(tile_id)

    def remove_running(self, tile_id: str) -> None:
        with self.lock:
            self._running_ids.discard(tile_id)

    def running_snapshot(self) -> set[str]:
        with self.lock:
            return set(self._running_ids)


def plan_england_tiles(config: dict, mask_path: str | Path) -> list[Tile]:
    """Build and land-filter the deterministic national core tile schedule."""
    config = normalize_production_config(config, mask_path=mask_path, require_mask=True)
    mask_path = Path(config["england_mask_100m_path"])
    with rasterio.open(mask_path) as dataset:
        extent = tuple(dataset.bounds)
    tiles = make_tiles(
        extent,
        tile_size_m=config["tile_size_m"],
        source_resolution_m=config["pilot_resolution_m"],
        output_resolution_m=config["output_resolution_m"],
    )
    return [tile for tile in tiles if tile_intersects_land(mask_path, tile.bbox)]


class _RateLimiter:
    def __init__(self, interval_s: float):
        self.interval_s = max(0.0, float(interval_s))
        self._lock = threading.Lock()
        self._last = 0.0

    def wait(self) -> None:
        with self._lock:
            now = time.monotonic()
            delay = self.interval_s - (now - self._last)
            if delay > 0:
                time.sleep(delay)
            self._last = time.monotonic()


def _manifest_payload(config: dict, mask_path: Path, output_root: Path, tiles: list[Tile],
                      build_identity: dict | None = None) -> dict:
    config = normalize_production_config(config, mask_path=mask_path, require_mask=True)
    build_identity = build_identity or compute_build_identity(config, mask_path, normalized=True)
    return {
        "created_at": _now(),
        "updated_at": _now(),
        "crs": config["crs"],
        "tile_size_m": config["tile_size_m"],
        "source_resolution_m": config["pilot_resolution_m"],
        "output_resolution_m": config["output_resolution_m"],
        "land_mask": str(mask_path),
        "storage": {
            "schema_version": STORAGE_SCHEMA_VERSION,
            "output_root": str(_canonical_path(output_root)),
        },
        "tile_count": len(tiles),
        "selected_tile_count": len(tiles),
        "build_identity": build_identity,
        "tiles": {
            tile.tile_id: {
                "tile": tile.to_dict(),
                "status": "pending",
                "attempts": 0,
                "retry_count": 0,
                "errors": [],
                "output": None,
            }
            for tile in tiles
        },
    }


def _is_within(path: str, parent: str) -> bool:
    try:
        return os.path.commonpath((path, parent)) == parent
    except ValueError:
        return False


def _validate_stored_output_paths(
    manifest: dict, expected_by_id: dict[str, Tile], output_root: Path
) -> None:
    canonical_root = canonical_resource_path(output_root)
    tiles_directory = _canonical_path(output_root / "tiles")
    canonical_tiles_directory = canonical_resource_path(tiles_directory)
    if not _is_within(canonical_tiles_directory, canonical_root):
        raise ValueError(
            "Stored tile outputs resolve through a symlink or junction outside the owned "
            "output root; existing outputs were left untouched."
        )

    for tile_id in sorted(expected_by_id):
        record = manifest["tiles"][tile_id]
        output = record.get("output")
        if record.get("status") == "complete" and output is None:
            raise ValueError(
                f"Completed manifest record {tile_id} has no output path; existing outputs "
                "were left untouched."
            )
        if output is None:
            continue
        if not isinstance(output, str) or not output.strip():
            raise ValueError(
                f"Stored output path for {tile_id} is invalid; existing outputs were left untouched."
            )
        try:
            actual = canonical_resource_path(output)
            expected = canonical_resource_path(
                output_root / "tiles" / f"{tile_id}.tif"
            )
        except (TypeError, ValueError, OSError) as exc:
            raise ValueError(
                f"Stored output path for {tile_id} is invalid; existing outputs were left untouched."
            ) from exc
        if not _is_within(actual, canonical_root):
            raise ValueError(
                f"Stored output path for {tile_id} resolves outside the owned output root; "
                "existing outputs were left untouched."
            )
        if not _is_within(actual, canonical_tiles_directory):
            raise ValueError(
                f"Stored output path for {tile_id} resolves through a symlink or junction "
                "outside the owned tiles directory; existing outputs were left untouched."
            )
        if actual != expected:
            raise ValueError(
                f"Stored output path for {tile_id} does not match its scheduled tile path; "
                "existing outputs were left untouched."
            )


def _load_or_create_manifest(path: Path, config: dict, mask_path: Path, output_root: Path,
                             tiles: list[Tile], build_identity: dict | None = None) -> dict:
    config = normalize_production_config(config, mask_path=mask_path, require_mask=True)
    build_identity = build_identity or compute_build_identity(config, mask_path, normalized=True)
    output_root = _canonical_path(output_root)
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        storage = manifest.get("storage")
        if (
            not isinstance(storage, dict)
            or not isinstance(storage.get("schema_version"), int)
            or isinstance(storage.get("schema_version"), bool)
            or storage.get("schema_version") != STORAGE_SCHEMA_VERSION
            or not isinstance(storage.get("output_root"), str)
            or not storage.get("output_root").strip()
        ):
            raise ValueError(
                "Cannot resume a legacy or unverified manifest without supported storage ownership metadata. "
                "Existing outputs were left untouched; use the matching output root or start "
                "a distinct build in an empty location."
            )
        try:
            stored_root = _canonical_path(storage["output_root"])
        except (TypeError, ValueError, OSError) as exc:
            raise ValueError(
                "Cannot resume a manifest with invalid storage ownership metadata. Existing "
                "outputs were left untouched; use the matching output root or start a distinct "
                "build in an empty location."
            ) from exc
        if stored_root != output_root:
            raise ValueError(
                "Manifest storage ownership mismatch: the requested output root does not match "
                f"the manifest's recorded root ({stored_root}). Existing outputs were left "
                "untouched; use the matching output root or start a distinct build in an empty "
                "location."
            )
        stored_identity = manifest.get("build_identity")
        if not isinstance(stored_identity, dict) or not stored_identity.get("fingerprint"):
            raise ValueError(
                "Cannot resume a legacy or unverified manifest without a build fingerprint. "
                "Existing outputs were left untouched; use a new output root/manifest for a new build."
            )
        stored_payload = stored_identity.get("payload")
        stored_fingerprint = stored_identity.get("fingerprint")
        if not isinstance(stored_payload, dict):
            raise ValueError(
                "Existing manifest has an inconsistent build identity payload. "
                "Existing outputs were left untouched; use a new output root/manifest for a new build."
            )
        try:
            recalculated = fingerprint_payload(stored_payload)
        except ValueError as exc:
            raise ValueError(
                "Existing manifest has an invalid build identity payload. "
                "Existing outputs were left untouched; use a new output root/manifest for a new build."
            ) from exc
        if stored_fingerprint != recalculated:
            raise ValueError(
                "Existing manifest has an inconsistent build identity fingerprint/payload. "
                "Existing outputs were left untouched; use a new output root/manifest for a new build."
            )
        if stored_identity.get("schema_version") != BUILD_IDENTITY_SCHEMA_VERSION:
            raise ValueError(
                "Existing manifest uses an unsupported build identity schema. "
                "Existing outputs were left untouched; use a new output root/manifest for a new build."
            )
        if stored_fingerprint != build_identity["fingerprint"]:
            changed = identity_differences(stored_payload, build_identity["payload"])
            suffix = f" Changed identity fields include: {', '.join(changed)}." if changed else ""
            raise ValueError(
                "Build identity mismatch; the requested scientific build is incompatible with "
                f"the existing manifest.{suffix} Existing outputs were left untouched; use a new "
                "output root/manifest for a new build."
            )

        existing = manifest.get("tiles")
        if not isinstance(existing, dict):
            raise ValueError(
                "Existing manifest has no valid full tile schedule. Existing outputs were left untouched; "
                "use a new output root/manifest for a new build."
            )
        expected_by_id = {tile.tile_id: tile for tile in tiles}
        existing_ids = set(existing)
        unexpected = sorted(existing_ids - set(expected_by_id))
        missing = sorted(set(expected_by_id) - existing_ids)
        if unexpected or missing:
            raise ValueError(
                "Existing manifest tile schedule is incompatible with the current full schedule "
                f"(missing={missing}, unexpected={unexpected}). Existing outputs were left untouched; "
                "use a new output root/manifest for a new build."
            )
        if int(manifest.get("tile_count", -1)) != len(tiles):
            raise ValueError(
                "Existing manifest tile_count does not match the current full schedule. "
                "Existing outputs were left untouched; use a new output root/manifest for a new build."
            )
        expected_bands = expected_band_schema(config)
        for tile_id, tile in expected_by_id.items():
            record = existing[tile_id]
            if not isinstance(record, dict) or record.get("tile") != tile.to_dict():
                raise ValueError(
                    f"Existing manifest tile definition for {tile_id} is incompatible with the current "
                    "schedule. Existing outputs were left untouched; use a new output root/manifest "
                    "for a new build."
                )
            stored_bands = record.get("bands")
            if stored_bands is not None and list(stored_bands) != expected_bands:
                raise ValueError(
                    f"Existing manifest band schema for {tile_id} is incompatible with current "
                    f"configuration (expected {expected_bands}, got {stored_bands}). Existing outputs "
                    "were left untouched; use a new output root/manifest for a new build."
                )
        _validate_stored_output_paths(manifest, expected_by_id, output_root)
        manifest["land_mask"] = str(mask_path)
        manifest["updated_at"] = _now()
        return manifest
    return _manifest_payload(config, mask_path, output_root, tiles, build_identity)


def _tile_from_record(record: dict) -> Tile:
    value = record["tile"]
    return Tile(
        tile_id=value["tile_id"], row=int(value["row"]), col=int(value["col"]),
        bbox=tuple(float(v) for v in value["bbox_epsg27700"]),
        source_resolution_m=int(value["source_resolution_m"]),
        output_resolution_m=int(value["output_resolution_m"]),
    )


def _append_error_log(path: Path, tile_id: str, message: str) -> None:
    with _ERROR_LOG_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as stream:
            stream.write(f"{_now()}\t{tile_id}\t{message}\n")


def _remove_staging(path: Path) -> None:
    if path.exists():
        path.unlink()


def _preflight_new_output_root(output_root: Path, manifest_path: Path) -> None:
    """Refuse a new manifest when reserved production locations already hold state."""
    if manifest_path.exists():
        return
    if output_root.exists() and not output_root.is_dir():
        raise ValueError(
            f"Output location already contains production state at {output_root}; it was left untouched. "
            "Resume requires its matching manifest, or a genuinely empty new output location."
        )
    if not output_root.exists():
        return

    standard_manifest = output_root / "tile_status_manifest.json"
    try:
        if standard_manifest.exists() and standard_manifest.resolve() != manifest_path.resolve():
            raise ValueError(
                f"Output location already contains production state in {standard_manifest}; "
                "it was left untouched. Resume requires its matching manifest, or a genuinely empty "
                "new output location."
            )
        for reserved_name in ("tiles", "temporary_10m"):
            reserved = output_root / reserved_name
            if not reserved.exists():
                continue
            if not reserved.is_dir():
                raise ValueError(
                    f"Output location already contains production state: {reserved} is not a directory; "
                    "it was left untouched. Resume requires its matching manifest, or a genuinely empty "
                    "new output location."
                )
            if any(reserved.iterdir()):
                raise ValueError(
                    f"Output location already contains production state in {reserved}; it was left untouched. "
                    "Resume requires its matching manifest, or a genuinely empty new output location."
                )
    except OSError as exc:
        raise ValueError(
            f"Could not safely inspect output location {output_root}; it was left untouched. "
            "Resume requires its matching manifest, or a genuinely empty new output location."
        ) from exc


def _ensure_owned_directory(path: Path, output_root: Path) -> None:
    if path.exists():
        if not path.is_dir():
            raise ValueError(f"Owned output location is not a directory: {path}")
        if not _is_within(canonical_resource_path(path), canonical_resource_path(output_root)):
            raise ValueError(
                f"Owned output location resolves outside the output root: {path}; "
                "existing outputs were left untouched."
            )
        return
    path.mkdir(parents=True, exist_ok=True)
    if not _is_within(canonical_resource_path(path), canonical_resource_path(output_root)):
        raise ValueError(
            f"Created output location resolves outside the output root: {path}"
        )


def _cleanup_staging(output_dir: Path, temp_dir: Path) -> None:
    canonical_output_dir = canonical_resource_path(output_dir)
    canonical_temp_dir = canonical_resource_path(temp_dir)
    for stale_dir in temp_dir.iterdir():
        resolved = canonical_resource_path(stale_dir)
        if stale_dir.is_symlink():
            # Never recurse through a link during cleanup. Leaving it is safer
            # than interpreting an external target as stale runner state.
            continue
        if not _is_within(resolved, canonical_temp_dir):
            raise ValueError(
                f"Refusing to clean staging entry outside the owned temporary directory: {stale_dir}"
            )
        if stale_dir.is_dir():
            shutil.rmtree(stale_dir)

    for stale_output in output_dir.glob(".*.attempt-*.tif"):
        if stale_output.is_symlink():
            continue
        resolved = canonical_resource_path(stale_output)
        if not _is_within(resolved, canonical_output_dir):
            raise ValueError(
                f"Refusing to clean staged output outside the owned tiles directory: {stale_output}"
            )
        if stale_output.is_file():
            stale_output.unlink()


def _run_one(tile: Tile, config: dict, output_dir: Path, temp_dir: Path,
             state: _ManifestState, error_log: Path, limiter: _RateLimiter) -> dict:
    runner = config.get("runner", {})
    max_attempts = max(1, int(runner.get("max_attempts", 4)))
    base_backoff = max(0.0, float(runner.get("retry_base_backoff_s", 5.0)))
    max_backoff = max(0.0, float(runner.get("retry_max_backoff_s", 120.0)))
    final_path = output_dir / f"{tile.tile_id}.tif"
    state.update_record(tile.tile_id, lambda record: record.update(
        status="running", started_at=_now(), last_error=None
    ))

    for local_attempt in range(1, max_attempts + 1):
        def begin_attempt(record):
            attempt_number = int(record.get("attempts", 0)) + 1
            record.update(
                attempts=attempt_number,
                retry_count=max(0, attempt_number - 1),
                last_attempt_at=_now(),
            )
            return attempt_number

        attempt_number = state.update_record(tile.tile_id, begin_attempt)
        staging_path = output_dir / f".{tile.tile_id}.attempt-{attempt_number}.tif"
        _remove_staging(staging_path)
        try:
            limiter.wait()
            result = process_tile(tile, config, staging_path, temp_root=temp_dir)
            validation = validate_tile_output(staging_path, tile, config, result["bands"])
            os.replace(staging_path, final_path)
            final_validation = validate_tile_output(final_path, tile, config, result["bands"])
            state.update_record(tile.tile_id, lambda record: record.update(
                status="complete",
                completed_at=_now(),
                output=str(_canonical_path(final_path)),
                bands=result["bands"],
                validation=final_validation,
                source_info=result.get("source_info", {}),
                temporary_10m_discarded=result.get("temporary_10m_discarded", False),
                land_mask_applied=result.get("land_mask_applied", False),
                last_error=None,
            ))
            return state.record_snapshot(tile.tile_id)
        except Exception as exc:
            _remove_staging(staging_path)
            message = f"{type(exc).__name__}: {exc}"
            state.update_record(tile.tile_id, lambda record: (
                record.update(status="failed", last_error=message),
                record.setdefault("errors", []).append({
                    "at": _now(), "attempt": attempt_number, "error": message,
                }),
            ))
            _append_error_log(error_log, tile.tile_id, message)
            if local_attempt < max_attempts:
                state.update_record(tile.tile_id, lambda record: record.update(status="running"))
                delay = min(max_backoff, base_backoff * (2 ** (local_attempt - 1)))
                if delay > 0:
                    time.sleep(delay)
    state.update_record(tile.tile_id, lambda record: record.update(finished_at=_now()))
    return state.record_snapshot(tile.tile_id)


def _validate_completed(config: dict, record: dict, scheduled_tile: Tile | None = None) -> bool:
    if record.get("status") != "complete" or not record.get("output"):
        return False
    try:
        tile = scheduled_tile or _tile_from_record(record)
        if scheduled_tile is not None and record.get("tile") != scheduled_tile.to_dict():
            return False
        validate_tile_output(record["output"], tile, config, expected_band_schema(config))
        return True
    except Exception:
        return False


def run_batch(config: dict, output_root: str | Path, manifest_path: str | Path,
              mask_path: str | Path, tile_ids: list[str] | None = None,
              failed_only: bool = False, limit: int | None = None,
              workers: int | None = None) -> dict:
    """Run a resumable, atomic, land-filtered batch of production tiles."""
    output_root = _canonical_path(output_root)
    output_dir = output_root / "tiles"
    temp_dir = output_root / "temporary_10m"
    manifest_path = _canonical_path(manifest_path)
    effective_config = normalize_production_config(
        config, mask_path=mask_path, require_mask=True
    )
    mask_path = Path(effective_config["england_mask_100m_path"])
    # Identity and schedule checks must happen before any cleanup or manifest
    # rewrite, so an incompatible resume leaves all existing outputs intact.
    build_identity = compute_build_identity(effective_config, mask_path, normalized=True)
    all_tiles = plan_england_tiles(effective_config, mask_path)
    all_tile_ids = {tile.tile_id for tile in all_tiles}
    requested_ids = set(tile_ids or [])
    selected = [tile for tile in all_tiles if tile_ids is None or tile.tile_id in requested_ids]
    if tile_ids is not None:
        missing = sorted(requested_ids - all_tile_ids)
        if missing:
            raise ValueError(f"Requested tile IDs are not England-intersecting or do not exist: {missing}")
    if limit is not None:
        selected = selected[:int(limit)]
    with acquire_resource_locks(
        [("output root", output_root), ("manifest", manifest_path)]
    ):
        _preflight_new_output_root(output_root, manifest_path)
        manifest = _load_or_create_manifest(
            manifest_path, effective_config, mask_path, output_root, all_tiles, build_identity
        )
        state = _ManifestState(manifest, manifest_path)
        state.publish()

        _ensure_owned_directory(output_dir, output_root)
        _ensure_owned_directory(temp_dir, output_root)
        # A process killed during a request can leave a TemporaryDirectory behind.
        # These are internal raw inputs with no complete status and are safe to
        # discard after identity and schedule validation; staged outputs are likewise
        # never valid output.
        _cleanup_staging(output_dir, temp_dir)
        state.update_manifest(lambda current: current.update(
            selected_tile_count=len(selected), updated_at=_now()
        ))
        error_log = manifest_path.with_name("tile_errors.log")

        todo: list[Tile] = []
        skipped_complete = 0
        for tile in selected:
            record = state.record_snapshot(tile.tile_id)
            if _validate_completed(effective_config, record, tile):
                skipped_complete += 1
                continue
            if failed_only and record.get("status") != "failed":
                continue
            state.update_record(tile.tile_id, lambda current: current.update(status="pending"))
            todo.append(tile)
        if not todo:
            state.update_manifest(lambda current: current.update(updated_at=_now()))
            print(
                f"England run: {skipped_complete}/{len(selected)} tiles already complete; nothing to do",
                flush=True,
            )
            return {
                "scheduled": len(selected),
                "total_scheduled": len(all_tiles),
                "processed": 0,
                "skipped_complete": skipped_complete,
                "failed": 0,
            }

        runner = effective_config.get("runner", {})
        worker_count = max(1, int(workers if workers is not None else runner.get("max_workers", 1)))
        limiter = _RateLimiter(float(runner.get("min_tile_start_interval_s", 1.0)))
        processed = 0
        failed = 0
        print(
            f"England run: {skipped_complete} complete, {len(todo)} to process, "
            f"{len(selected)} scheduled; workers={worker_count}",
            flush=True,
        )

        def invoke(tile: Tile):
            state.add_running(tile.tile_id)
            try:
                return _run_one(tile, effective_config, output_dir, temp_dir,
                                state, error_log, limiter)
            finally:
                state.remove_running(tile.tile_id)

        try:
            executor = ThreadPoolExecutor(max_workers=worker_count)
            futures = {}
            try:
                futures = {executor.submit(invoke, tile): tile for tile in todo}
                for future in as_completed(futures):
                    record = future.result()
                    if record.get("status") == "complete":
                        processed += 1
                        print(
                            f"[{processed + skipped_complete}/{len(selected)}] "
                            f"{record['tile']['tile_id']} complete "
                            f"(attempts={record.get('attempts', 0)})",
                            flush=True,
                        )
                    else:
                        failed += 1
                        print(
                            f"[{processed + failed + skipped_complete}/{len(selected)}] "
                            f"{record['tile']['tile_id']} failed "
                            f"(attempts={record.get('attempts', 0)}): {record.get('last_error')}",
                            flush=True,
                        )
            except KeyboardInterrupt:
                for future in futures:
                    future.cancel()
                raise
            finally:
                executor.shutdown(wait=True, cancel_futures=True)
        except KeyboardInterrupt:
            for tile_id in state.running_snapshot():
                record = state.record_snapshot(tile_id)
                if record.get("status") == "running":
                    state.update_record(tile_id, lambda current: current.update(
                        status="pending",
                        last_error="Interrupted by user; safe to resume",
                    ))
            state.update_manifest(lambda current: current.update(updated_at=_now()))
            raise

        state.update_manifest(lambda current: current.update(
            updated_at=_now(),
            last_run={
                "at": _now(), "processed": processed, "failed": failed,
                "scheduled": len(todo), "selected_tile_count": len(selected),
                "total_scheduled_tile_count": len(all_tiles), "workers": worker_count,
            },
        ))
        return {
            "scheduled": len(selected), "total_scheduled": len(all_tiles), "processed": processed,
            "failed": failed, "skipped_complete": len(selected) - len(todo),
            "manifest": str(manifest_path),
        }
