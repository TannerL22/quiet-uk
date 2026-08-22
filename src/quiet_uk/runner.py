from __future__ import annotations

import json
import os
import shutil
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

import rasterio

from .land_mask import tile_intersects_land
from .tiling import Tile, make_tiles, process_tile, validate_tile_output


_MANIFEST_LOCK = threading.Lock()
_ERROR_LOG_LOCK = threading.Lock()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _atomic_json_write(path: Path, payload: dict) -> None:
    with _MANIFEST_LOCK:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_name(f".{path.name}.tmp")
        temporary.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        os.replace(temporary, path)


def plan_england_tiles(config: dict, mask_path: str | Path) -> list[Tile]:
    """Build and land-filter the deterministic national core tile schedule."""
    with rasterio.open(mask_path) as dataset:
        extent = tuple(dataset.bounds)
    tiles = make_tiles(
        extent,
        tile_size_m=int(config.get("tile_size_m", 10_000)),
        source_resolution_m=int(config.get("pilot_resolution_m", 10)),
        output_resolution_m=int(config.get("output_resolution_m", 100)),
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


def _manifest_payload(config: dict, mask_path: Path, tiles: list[Tile]) -> dict:
    return {
        "created_at": _now(),
        "updated_at": _now(),
        "crs": config.get("crs", "EPSG:27700"),
        "tile_size_m": int(config.get("tile_size_m", 10_000)),
        "source_resolution_m": int(config.get("pilot_resolution_m", 10)),
        "output_resolution_m": int(config.get("output_resolution_m", 100)),
        "land_mask": str(mask_path),
        "tile_count": len(tiles),
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


def _load_or_create_manifest(path: Path, config: dict, mask_path: Path,
                             tiles: list[Tile]) -> dict:
    if path.exists():
        manifest = json.loads(path.read_text(encoding="utf-8"))
        existing = manifest.setdefault("tiles", {})
        for tile in tiles:
            existing.setdefault(tile.tile_id, {
                "tile": tile.to_dict(), "status": "pending", "attempts": 0,
                "retry_count": 0, "errors": [], "output": None,
            })
        manifest["tile_count"] = len(tiles)
        manifest["land_mask"] = str(mask_path)
        manifest["updated_at"] = _now()
        return manifest
    manifest = _manifest_payload(config, mask_path, tiles)
    _atomic_json_write(path, manifest)
    return manifest


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


def _run_one(tile: Tile, config: dict, output_dir: Path, temp_dir: Path,
             status: dict, manifest: dict, manifest_path: Path,
             error_log: Path, limiter: _RateLimiter) -> dict:
    runner = config.get("runner", {})
    max_attempts = max(1, int(runner.get("max_attempts", 4)))
    base_backoff = max(0.0, float(runner.get("retry_base_backoff_s", 5.0)))
    max_backoff = max(0.0, float(runner.get("retry_max_backoff_s", 120.0)))
    final_path = output_dir / f"{tile.tile_id}.tif"
    status["status"] = "running"
    status["started_at"] = _now()
    status["last_error"] = None
    _atomic_json_write(manifest_path, manifest)

    for local_attempt in range(1, max_attempts + 1):
        attempt_number = int(status.get("attempts", 0)) + 1
        status["attempts"] = attempt_number
        status["retry_count"] = max(0, attempt_number - 1)
        status["last_attempt_at"] = _now()
        staging_path = output_dir / f".{tile.tile_id}.attempt-{attempt_number}.tif"
        _remove_staging(staging_path)
        _atomic_json_write(manifest_path, manifest)
        try:
            limiter.wait()
            result = process_tile(tile, config, staging_path, temp_root=temp_dir)
            validation = validate_tile_output(staging_path, tile, config, result["bands"])
            os.replace(staging_path, final_path)
            final_validation = validate_tile_output(final_path, tile, config, result["bands"])
            status.update({
                "status": "complete",
                "completed_at": _now(),
                "output": str(final_path),
                "bands": result["bands"],
                "validation": final_validation,
                "source_info": result.get("source_info", {}),
                "temporary_10m_discarded": result.get("temporary_10m_discarded", False),
                "land_mask_applied": result.get("land_mask_applied", False),
                "last_error": None,
            })
            _atomic_json_write(manifest_path, manifest)
            return status
        except Exception as exc:
            _remove_staging(staging_path)
            message = f"{type(exc).__name__}: {exc}"
            status["status"] = "failed"
            status["last_error"] = message
            status.setdefault("errors", []).append({
                "at": _now(), "attempt": attempt_number, "error": message,
            })
            _append_error_log(error_log, tile.tile_id, message)
            _atomic_json_write(manifest_path, manifest)
            if local_attempt < max_attempts:
                status["status"] = "running"
                _atomic_json_write(manifest_path, manifest)
                delay = min(max_backoff, base_backoff * (2 ** (local_attempt - 1)))
                if delay > 0:
                    time.sleep(delay)
    status["finished_at"] = _now()
    _atomic_json_write(manifest_path, manifest)
    return status


def _validate_completed(config: dict, record: dict) -> bool:
    if record.get("status") != "complete" or not record.get("output"):
        return False
    try:
        tile = _tile_from_record(record)
        validate_tile_output(record["output"], tile, config, record.get("bands"))
        return True
    except Exception:
        return False


def run_batch(config: dict, output_root: str | Path, manifest_path: str | Path,
              mask_path: str | Path, tile_ids: list[str] | None = None,
              failed_only: bool = False, limit: int | None = None,
              workers: int | None = None) -> dict:
    """Run a resumable, atomic, land-filtered batch of production tiles."""
    output_root = Path(output_root)
    output_dir = output_root / "tiles"
    temp_dir = output_root / "temporary_10m"
    manifest_path = Path(manifest_path)
    mask_path = Path(mask_path)
    output_dir.mkdir(parents=True, exist_ok=True)
    temp_dir.mkdir(parents=True, exist_ok=True)
    # A process killed during a request can leave a TemporaryDirectory behind.
    # These are internal raw inputs with no complete status and are safe to
    # discard before resuming; staged outputs are likewise never valid output.
    for stale_dir in temp_dir.iterdir():
        if stale_dir.is_dir():
            shutil.rmtree(stale_dir)
    for stale_output in output_dir.glob(".*.attempt-*.tif"):
        stale_output.unlink()
    all_tiles = plan_england_tiles(config, mask_path)
    selected = [tile for tile in all_tiles if tile_ids is None or tile.tile_id in set(tile_ids)]
    if tile_ids is not None:
        missing = sorted(set(tile_ids) - {tile.tile_id for tile in selected})
        if missing:
            raise ValueError(f"Requested tile IDs are not England-intersecting or do not exist: {missing}")
    if limit is not None:
        selected = selected[:int(limit)]
    manifest = _load_or_create_manifest(manifest_path, config, mask_path, selected)
    manifest["selected_tile_count"] = len(selected)
    manifest["updated_at"] = _now()
    error_log = manifest_path.with_name("tile_errors.log")
    _atomic_json_write(manifest_path, manifest)

    todo: list[Tile] = []
    for tile in selected:
        record = manifest["tiles"][tile.tile_id]
        if _validate_completed(config, record):
            continue
        if failed_only and record.get("status") != "failed":
            continue
        record["status"] = "pending"
        todo.append(tile)
    if not todo:
        manifest["updated_at"] = _now()
        _atomic_json_write(manifest_path, manifest)
        return {"scheduled": len(selected), "processed": 0, "skipped_complete": len(selected), "failed": 0}

    runner = config.get("runner", {})
    worker_count = max(1, int(workers if workers is not None else runner.get("max_workers", 1)))
    limiter = _RateLimiter(float(runner.get("min_tile_start_interval_s", 1.0)))
    processed = 0
    failed = 0
    running_ids: set[str] = set()

    def invoke(tile: Tile):
        record = manifest["tiles"][tile.tile_id]
        running_ids.add(tile.tile_id)
        try:
            return _run_one(tile, config, output_dir, temp_dir, record, manifest,
                            manifest_path, error_log, limiter)
        finally:
            running_ids.discard(tile.tile_id)

    try:
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {executor.submit(invoke, tile): tile for tile in todo}
            for future in as_completed(futures):
                record = future.result()
                if record.get("status") == "complete":
                    processed += 1
                else:
                    failed += 1
    except KeyboardInterrupt:
        for tile_id in list(running_ids):
            record = manifest["tiles"][tile_id]
            record["status"] = "pending"
            record["last_error"] = "Interrupted by user; safe to resume"
        manifest["updated_at"] = _now()
        _atomic_json_write(manifest_path, manifest)
        raise

    manifest["updated_at"] = _now()
    manifest["last_run"] = {
        "at": _now(), "processed": processed, "failed": failed,
        "scheduled": len(todo), "workers": worker_count,
    }
    _atomic_json_write(manifest_path, manifest)
    return {
        "scheduled": len(selected), "processed": processed,
        "failed": failed, "skipped_complete": len(selected) - len(todo),
        "manifest": str(manifest_path),
    }
