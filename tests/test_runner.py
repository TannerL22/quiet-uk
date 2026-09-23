import json
import copy
import shutil
import threading
from concurrent.futures import as_completed as real_as_completed
from pathlib import Path

import numpy as np
import pytest
import rasterio
from rasterio.transform import Affine

from quiet_uk.build_identity import compute_build_identity
from quiet_uk import runner, tiling
from quiet_uk.locking import canonical_resource_path
from quiet_uk.tiling import make_tiles, process_tile


def _geo_tiff_bytes(data, transform, nodata=None):
    from rasterio.io import MemoryFile

    mem = MemoryFile()
    profile = {
        "driver": "GTiff", "height": data.shape[0], "width": data.shape[1],
        "count": 1, "dtype": str(data.dtype), "crs": "EPSG:27700",
        "transform": transform,
    }
    if nodata is not None:
        profile["nodata"] = nodata
    with mem.open(**profile) as dst:
        dst.write(data, 1)
    payload = mem.read()
    mem.close()
    return payload


def _synthetic_source_payloads(tile):
    source_transform = Affine(10, 0, tile.bbox[0], 0, -10, tile.bbox[3])
    road = np.full(tile.source_shape, 50.0, dtype="float32")
    rail = np.full(tile.source_shape, 45.0, dtype="float32")
    airport = np.full(tile.source_shape, 55.0, dtype="float32")
    return {
        "road": _geo_tiff_bytes(road, source_transform, nodata=-96.0),
        "rail": _geo_tiff_bytes(rail, source_transform, nodata=-96.0),
        "airport": _geo_tiff_bytes(airport, source_transform, nodata=3.4e38),
    }


def _write_mask(path, bbox=(0, 0, 10000, 10000), value=1):
    height = int((bbox[3] - bbox[1]) / 100)
    width = int((bbox[2] - bbox[0]) / 100)
    profile = {
        "driver": "GTiff", "height": height, "width": width, "count": 1,
        "dtype": "uint8", "crs": "EPSG:27700",
        "transform": Affine(100, 0, bbox[0], 0, -100, bbox[3]), "nodata": 0,
    }
    with rasterio.open(path, "w", **profile) as dst:
        dst.write(np.full((height, width), value, dtype="uint8"), 1)


def test_outside_declared_coverage_skips_wcs_request(monkeypatch, tmp_path):
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    called = []

    def fail_if_called(*args, **kwargs):
        called.append(True)
        raise AssertionError("out-of-coverage source should not be requested")

    monkeypatch.setattr(tiling, "get_coverage", fail_if_called)
    target = tiling.tile_grid(tile, "EPSG:27700")
    array, info = tiling._load_source_for_tile(
        "airport", tile,
        {
            "wcs": {"airport": "unused"},
            "coverage_ids": {"airport": "unused"},
            "coverage_bounds_epsg27700": {"airport": [5000, 5000, 6000, 6000]},
        }, target, tmp_path,
    )
    assert not called
    assert np.isnan(array).all()
    assert info["skipped_outside_declared_coverage"] is True


def test_process_tile_masks_final_cells_with_land_mask(monkeypatch, tmp_path):
    tile = make_tiles((0, 0, 1000, 1000), tile_size_m=1000)[0]
    payloads = _synthetic_source_payloads(tile)

    def fake_get_coverage(url, coverage_id, *args, **kwargs):
        return payloads[coverage_id]

    monkeypatch.setattr(tiling, "get_coverage", fake_get_coverage)
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path, bbox=(0, 0, 1000, 1000))
    config = {
        "crs": "EPSG:27700", "pilot_resolution_m": 10, "output_resolution_m": 100,
        "reporting_threshold_db": {"road": 40.0, "rail": 40.0, "airport": None},
        "wcs": {name: name for name in ("road", "rail", "airport")},
        "coverage_ids": {name: name for name in ("road", "rail", "airport")},
        "wcs_versions": {name: "1.0.0" for name in ("road", "rail", "airport")},
        "england_mask_100m_path": str(mask_path),
    }
    output = tmp_path / "tile.tif"
    result = process_tile(tile, config, output, temp_root=tmp_path / "temp")
    assert result["land_mask_applied"] is True
    with rasterio.open(output) as dataset:
        assert np.all(dataset.read() != -9999.0)

    # A second mask with only the north-west quarter as land must blank the rest.
    with rasterio.open(mask_path, "r+") as dataset:
        values = np.zeros((100, 100), dtype="uint8")
        values[:50, :50] = 1
        dataset.write(values, 1)
    output2 = tmp_path / "tile_masked.tif"
    process_tile(tile, config, output2, temp_root=tmp_path / "temp2")
    with rasterio.open(output2) as dataset:
        values = dataset.read(1)
        assert np.all(values[:5, :5] != -9999.0)
        assert np.all(values[5:, :] == -9999.0)
        assert np.all(values[:, 5:] == -9999.0)


def test_runner_retries_then_skips_valid_complete_tile(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = {
        "crs": "EPSG:27700", "pilot_resolution_m": 10, "output_resolution_m": 100,
        "tile_size_m": 10000,
        "england_mask_100m_path": str(mask_path),
        "wcs": {name: f"https://example.test/{name}" for name in ("road", "rail", "airport")},
        "coverage_ids": {name: f"{name}_Lden" for name in ("road", "rail", "airport")},
        "wcs_versions": {name: "1.0.0" for name in ("road", "rail", "airport")},
        "reporting_threshold_db": {"road": 40.0, "rail": 40.0, "airport": None},
        "runner": {"max_workers": 1, "min_tile_start_interval_s": 0,
                    "max_attempts": 2, "retry_base_backoff_s": 0,
                    "retry_max_backoff_s": 0},
    }
    calls = []

    def fake_process(tile, config, path, temp_root=None):
        calls.append(tile.tile_id)
        if len(calls) == 1:
            raise RuntimeError("synthetic WCS failure")
        arrays = [np.full(tile.output_shape, 50.0, dtype="float32") for _ in range(4)]
        arrays[3][:] = 1.0
        tiling._write_float_bands(path, arrays, list(tiling.TILE_BANDS), tile, "EPSG:27700")
        return {"bands": list(tiling.TILE_BANDS), "source_info": {},
                "temporary_10m_discarded": True, "land_mask_applied": True}

    monkeypatch.setattr(runner, "process_tile", fake_process)
    manifest_path = tmp_path / "manifest.json"
    first = runner.run_batch(config, tmp_path / "outputs", manifest_path, mask_path)
    assert first["processed"] == 1
    assert first["failed"] == 0
    assert len(calls) == 2
    manifest = json.loads(manifest_path.read_text())
    record = next(iter(manifest["tiles"].values()))
    assert record["status"] == "complete"
    assert record["attempts"] == 2
    second = runner.run_batch(config, tmp_path / "outputs", manifest_path, mask_path)
    assert second["processed"] == 0
    assert second["skipped_complete"] == 1
    assert len(calls) == 2


def test_concurrent_workers_publish_coherent_manifest_snapshots(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path, bbox=(0, 0, 2000, 1000))
    config = _identity_config(mask_path, tile_size_m=1000)
    config["runner"].update(max_workers=2, max_attempts=2)
    call_counts = {}
    call_lock = threading.Lock()
    first_attempt_barrier = threading.Barrier(2)
    overlap = threading.Event()
    active = 0
    snapshots = []
    real_write = runner._atomic_json_write

    def capture_write(path, payload):
        snapshots.append(copy.deepcopy(payload))
        real_write(path, payload)

    def fake_process(tile, config, path, temp_root=None):
        nonlocal active
        with call_lock:
            attempt = call_counts.get(tile.tile_id, 0) + 1
            call_counts[tile.tile_id] = attempt
            active += 1
            if active >= 2:
                overlap.set()
        try:
            if attempt == 1:
                first_attempt_barrier.wait(timeout=10)
                if tile.tile_id == "r0000c0000":
                    raise RuntimeError("controlled transient failure")
            arrays = [np.full(tile.output_shape, 50.0, dtype="float32") for _ in range(4)]
            arrays[1][:] = 45.0
            arrays[3][:] = 1.0
            tiling._write_float_bands(path, arrays, list(tiling.TILE_BANDS), tile, "EPSG:27700")
            return {"bands": list(tiling.TILE_BANDS), "source_info": {}}
        finally:
            with call_lock:
                active -= 1

    monkeypatch.setattr(runner, "_atomic_json_write", capture_write)
    monkeypatch.setattr(runner, "process_tile", fake_process)
    output_root = tmp_path / "outputs"
    manifest_path = tmp_path / "manifest.json"
    result = runner.run_batch(config, output_root, manifest_path, mask_path)
    assert result["processed"] == 2
    assert overlap.is_set()

    for snapshot in snapshots:
        json.loads(json.dumps(snapshot))
    persisted = json.loads(manifest_path.read_text())
    assert all(record["status"] == "complete" for record in persisted["tiles"].values())
    assert all(record.get("output") and record.get("bands") and record.get("validation")
               for record in persisted["tiles"].values())
    failed_then_retried = persisted["tiles"]["r0000c0000"]
    assert failed_then_retried["attempts"] == 2
    assert failed_then_retried["retry_count"] == 1
    assert len(failed_then_retried["errors"]) == 1
    assert "controlled transient failure" in failed_then_retried["errors"][0]["error"]
    assert any(
        snapshot["tiles"]["r0000c0000"].get("errors")
        for snapshot in snapshots
    )

    resumed = runner.run_batch(config, output_root, manifest_path, mask_path)
    assert resumed["skipped_complete"] == 2
    assert call_counts == {"r0000c0000": 2, "r0000c0001": 1}


def _identity_config(mask_path, tile_size_m=10000):
    return {
        "crs": "EPSG:27700",
        "metric": "Lden",
        "pilot_resolution_m": 10,
        "output_resolution_m": 100,
        "tile_size_m": tile_size_m,
        "reporting_threshold_db": {"road": 40.0, "rail": 40.0, "airport": None},
        "wcs": {name: f"https://example.test/{name}" for name in ("road", "rail", "airport")},
        "coverage_ids": {name: f"{name}_Lden_all" for name in ("road", "rail", "airport")},
        "wcs_versions": {name: "1.0.0" for name in ("road", "rail", "airport")},
        "wcs_formats": {name: "GeoTIFF" for name in ("road", "rail", "airport")},
        "england_mask_100m_path": str(mask_path),
        "runner": {
            "max_workers": 1, "min_tile_start_interval_s": 0,
            "max_attempts": 1, "retry_base_backoff_s": 0,
            "retry_max_backoff_s": 0, "progress": True,
        },
    }


def _successful_fake_process(calls):
    def fake_process(tile, config, path, temp_root=None):
        calls.append(tile.tile_id)
        arrays = [np.full(tile.output_shape, 50.0, dtype="float32") for _ in range(4)]
        arrays[1][:] = 45.0
        arrays[3][:] = 1.0
        tiling._write_float_bands(path, arrays, list(tiling.TILE_BANDS), tile, "EPSG:27700")
        return {"bands": list(tiling.TILE_BANDS), "source_info": {},
                "temporary_10m_discarded": True, "land_mask_applied": True}
    return fake_process


def test_new_manifest_has_build_identity_and_full_schedule_count(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path, bbox=(0, 0, 2000, 2000))
    config = _identity_config(mask_path, tile_size_m=1000)
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    manifest_path = tmp_path / "manifest.json"
    result = runner.run_batch(config, tmp_path / "outputs", manifest_path, mask_path, limit=1)

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    identity = manifest["build_identity"]
    assert identity["schema_version"] == 1
    assert identity["fingerprint"]
    assert identity["payload"]["output_band_schema"] == list(tiling.TILE_BANDS)
    assert identity["schema_version"] == 1
    assert manifest["storage"]["schema_version"] == runner.STORAGE_SCHEMA_VERSION
    assert manifest["storage"]["output_root"] == canonical_resource_path(tmp_path / "outputs")
    assert manifest["tile_count"] == 4
    assert manifest["selected_tile_count"] == 1
    assert len(manifest["tiles"]) == 4
    assert result["scheduled"] == 1
    assert result["total_scheduled"] == 4
    assert len(calls) == 1


def test_explicit_mask_is_authoritative_for_identity_schedule_and_processing(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    del config["england_mask_100m_path"]
    original = copy.deepcopy(config)
    captured_masks = []

    def fake_process(tile, effective_config, path, temp_root=None):
        captured_masks.append(effective_config["england_mask_100m_path"])
        return _successful_fake_process([])(tile, effective_config, path, temp_root)

    monkeypatch.setattr(runner, "process_tile", fake_process)
    manifest_path = tmp_path / "manifest.json"
    runner.run_batch(config, tmp_path / "outputs", manifest_path, mask_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    expected_identity = compute_build_identity(config, mask_path)
    assert captured_masks == [str(mask_path.resolve())]
    assert manifest["build_identity"] == expected_identity
    assert config == original


def test_existing_manifest_cannot_be_reused_with_different_output_root(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    first_root = tmp_path / "output-a"
    manifest_path = tmp_path / "manifest.json"
    runner.run_batch(config, first_root, manifest_path, mask_path)
    before_manifest = manifest_path.read_bytes()

    second_root = tmp_path / "output-b"
    marker = second_root / "temporary_10m" / "unrelated-run" / "marker.txt"
    marker.parent.mkdir(parents=True)
    marker.write_bytes(b"leave me")
    with pytest.raises(ValueError, match="storage ownership mismatch"):
        runner.run_batch(config, second_root, manifest_path, mask_path)
    assert manifest_path.read_bytes() == before_manifest
    assert marker.read_bytes() == b"leave me"
    assert len(calls) == 1


def test_storage_metadata_is_required_without_migration(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    output_root = tmp_path / "outputs"
    manifest_path = tmp_path / "manifest.json"
    runner.run_batch(config, output_root, manifest_path, mask_path)
    manifest = json.loads(manifest_path.read_text())
    del manifest["storage"]
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    before = manifest_path.read_bytes()
    with pytest.raises(ValueError, match="storage ownership metadata"):
        runner.run_batch(config, output_root, manifest_path, mask_path)
    assert manifest_path.read_bytes() == before
    assert len(calls) == 1


def test_stored_output_path_must_be_owned_and_match_scheduled_tile(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    output_root = tmp_path / "outputs"
    manifest_path = tmp_path / "manifest.json"
    runner.run_batch(config, output_root, manifest_path, mask_path)
    manifest = json.loads(manifest_path.read_text())
    tile_id = next(iter(manifest["tiles"]))
    manifest["tiles"][tile_id]["output"] = str(tmp_path / "outside.tif")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    before = manifest_path.read_bytes()
    with pytest.raises(ValueError, match="outside the owned output root"):
        runner.run_batch(config, output_root, manifest_path, mask_path)
    assert manifest_path.read_bytes() == before
    assert len(calls) == 1

    manifest["tiles"][tile_id]["output"] = str(output_root / "tiles" / "different.tif")
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    before = manifest_path.read_bytes()
    with pytest.raises(ValueError, match="scheduled tile path"):
        runner.run_batch(config, output_root, manifest_path, mask_path)
    assert manifest_path.read_bytes() == before
    assert len(calls) == 1


def test_existing_owned_output_symlink_to_external_location_is_rejected(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    output_root = tmp_path / "outputs"
    manifest_path = tmp_path / "manifest.json"
    runner.run_batch(config, output_root, manifest_path, mask_path)
    manifest_before = manifest_path.read_bytes()
    tile_id = next(iter(json.loads(manifest_before)["tiles"]))
    owned_output = output_root / "tiles" / f"{tile_id}.tif"
    external_output = tmp_path / "external.tif"
    external_output.write_bytes(b"external")
    owned_output.unlink()
    try:
        owned_output.symlink_to(external_output)
    except OSError:
        pytest.skip("symbolic links are unavailable in this Windows test environment")
    with pytest.raises(ValueError, match="outside the owned output root"):
        runner.run_batch(config, output_root, manifest_path, mask_path)
    assert manifest_path.read_bytes() == manifest_before
    assert external_output.read_bytes() == b"external"
    assert len(calls) == 1


def test_completed_record_without_output_is_rejected_and_nonselected_records_are_checked(
    monkeypatch, tmp_path
):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path, bbox=(0, 0, 2000, 2000))
    config = _identity_config(mask_path, tile_size_m=1000)
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    output_root = tmp_path / "outputs"
    manifest_path = tmp_path / "manifest.json"
    runner.run_batch(config, output_root, manifest_path, mask_path)
    manifest = json.loads(manifest_path.read_text())
    tile_ids = list(manifest["tiles"])
    manifest["tiles"][tile_ids[0]]["output"] = None
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    before = manifest_path.read_bytes()
    with pytest.raises(ValueError, match="has no output path"):
        runner.run_batch(
            config, output_root, manifest_path, mask_path, tile_ids=[tile_ids[1]]
        )
    assert manifest_path.read_bytes() == before
    assert len(calls) == 4


def test_equivalent_output_root_aliases_resume_the_same_owned_manifest(
    monkeypatch, tmp_path
):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    monkeypatch.chdir(tmp_path)
    runner.run_batch(config, Path("outputs"), Path("manifest.json"), mask_path)
    second = runner.run_batch(config, (tmp_path / "outputs").resolve(), tmp_path / "manifest.json", mask_path)
    assert second["skipped_complete"] == 1
    assert len(calls) == 1


def test_interruption_waits_for_workers_and_preserves_completed_records(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    config["runner"]["max_attempts"] = 1
    started = threading.Event()
    release = threading.Event()
    calls = []

    def fake_process(tile, config, path, temp_root=None):
        started.set()
        assert release.wait(timeout=10)
        calls.append(tile.tile_id)
        arrays = [np.full(tile.output_shape, 50.0, dtype="float32") for _ in range(4)]
        arrays[1][:] = 45.0
        arrays[3][:] = 1.0
        tiling._write_float_bands(path, arrays, list(tiling.TILE_BANDS), tile, "EPSG:27700")
        return {"bands": list(tiling.TILE_BANDS), "source_info": {}}

    def interrupting_as_completed(futures):
        assert started.wait(timeout=10)
        release.set()
        raise KeyboardInterrupt

    monkeypatch.setattr(runner, "process_tile", fake_process)
    monkeypatch.setattr(runner, "as_completed", interrupting_as_completed)
    output_root = tmp_path / "outputs"
    manifest_path = tmp_path / "manifest.json"
    with pytest.raises(KeyboardInterrupt):
        runner.run_batch(config, output_root, manifest_path, mask_path)

    manifest = json.loads(manifest_path.read_text())
    record = next(iter(manifest["tiles"].values()))
    assert record["status"] == "complete"
    assert Path(record["output"]).exists()
    assert len(calls) == 1

    monkeypatch.setattr(runner, "as_completed", real_as_completed)
    resumed = runner.run_batch(config, output_root, manifest_path, mask_path)
    assert resumed["skipped_complete"] == 1
    assert len(calls) == 1


def test_invalid_batch_configuration_is_rejected_before_processor(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    config["coverage_ids"]["road"] = None
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    manifest_path = tmp_path / "manifest.json"
    with pytest.raises(ValueError, match="coverage_ids.road"):
        runner.run_batch(config, tmp_path / "outputs", manifest_path, mask_path)
    assert not manifest_path.exists()
    assert not calls


def test_conflicting_configured_and_explicit_masks_are_rejected_before_mutation(monkeypatch, tmp_path):
    explicit_mask = tmp_path / "explicit-mask.tif"
    configured_mask = tmp_path / "configured-mask.tif"
    _write_mask(explicit_mask)
    _write_mask(configured_mask)
    config = _identity_config(configured_mask)
    output_root = tmp_path / "outputs"
    tiles_dir = output_root / "tiles"
    tiles_dir.mkdir(parents=True)
    final_marker = tiles_dir / "existing.tif"
    final_marker.write_bytes(b"final")
    staged_marker = output_root / "temporary_10m"
    staged_marker.mkdir()
    (staged_marker / "raw.tif").write_bytes(b"staging")
    manifest_path = tmp_path / "manifest.json"
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    with pytest.raises(ValueError, match="conflicts"):
        runner.run_batch(config, output_root, manifest_path, explicit_mask)
    assert not manifest_path.exists()
    assert final_marker.read_bytes() == b"final"
    assert (staged_marker / "raw.tif").read_bytes() == b"staging"
    assert not calls


@pytest.mark.parametrize("occupied_kind", ["final", "staged", "temporary"])
def test_new_manifest_refuses_occupied_reserved_output_state(monkeypatch, tmp_path, occupied_kind):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    output_root = tmp_path / "outputs"
    if occupied_kind == "final":
        occupied = output_root / "tiles"
        occupied.mkdir(parents=True)
        marker = occupied / "r0000c0000.tif"
        marker.write_bytes(b"final")
    elif occupied_kind == "staged":
        occupied = output_root / "tiles"
        occupied.mkdir(parents=True)
        marker = occupied / ".r0000c0000.attempt-1.tif"
        marker.write_bytes(b"staged")
    else:
        occupied = output_root / "temporary_10m"
        occupied.mkdir(parents=True)
        marker = occupied / "raw.tif"
        marker.write_bytes(b"temporary")
    manifest_path = tmp_path / "new-manifest.json"
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    with pytest.raises(ValueError, match="already contains production state"):
        runner.run_batch(config, output_root, manifest_path, mask_path)
    assert not manifest_path.exists()
    assert marker.read_bytes() in {b"final", b"staged", b"temporary"}
    assert not calls


@pytest.mark.parametrize("reserved_name", ["tiles", "temporary_10m"])
def test_new_manifest_refuses_reserved_path_regular_file(monkeypatch, tmp_path, reserved_name):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    reserved = output_root / reserved_name
    reserved.write_bytes(b"do not replace")
    manifest_path = tmp_path / "manifest.json"
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    with pytest.raises(ValueError, match="not a directory"):
        runner.run_batch(config, output_root, manifest_path, mask_path)
    assert not manifest_path.exists()
    assert reserved.read_bytes() == b"do not replace"
    assert not calls


def test_new_manifest_refuses_different_existing_standard_manifest(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    output_root = tmp_path / "outputs"
    output_root.mkdir()
    standard = output_root / "tile_status_manifest.json"
    standard.write_bytes(b"standard")
    manifest_path = tmp_path / "other-manifest.json"
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    with pytest.raises(ValueError, match="tile_status_manifest.json"):
        runner.run_batch(config, output_root, manifest_path, mask_path)
    assert not manifest_path.exists()
    assert standard.read_bytes() == b"standard"
    assert not calls


def test_empty_reserved_output_directories_permit_new_manifest(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    output_root = tmp_path / "outputs"
    (output_root / "tiles").mkdir(parents=True)
    (output_root / "temporary_10m").mkdir()
    (output_root / "unrelated.txt").write_bytes(b"keep")
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    result = runner.run_batch(config, output_root, tmp_path / "manifest.json", mask_path)
    assert result["processed"] == 1
    assert len(calls) == 1
    assert (output_root / "unrelated.txt").read_bytes() == b"keep"


def test_relative_and_absolute_references_to_same_mask_are_accepted(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    monkeypatch.chdir(tmp_path)
    config = _identity_config(Path("mask.tif"))
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    runner.run_batch(config, tmp_path / "outputs", tmp_path / "manifest.json", mask_path)
    assert len(calls) == 1
    assert config["england_mask_100m_path"] == "mask.tif"


def test_operational_runner_changes_permit_compatible_resume(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    manifest_path = tmp_path / "manifest.json"
    runner.run_batch(config, tmp_path / "outputs", manifest_path, mask_path)

    changed = copy.deepcopy(config)
    changed["runner"].update({"max_workers": 8, "max_attempts": 7, "retry_base_backoff_s": 99.0})
    second = runner.run_batch(changed, tmp_path / "outputs", manifest_path, mask_path)
    assert second["skipped_complete"] == 1
    assert len(calls) == 1


@pytest.mark.parametrize(
    "change",
    [
        lambda config: config["reporting_threshold_db"].update(road=41.0),
        lambda config: config["coverage_ids"].update(road="road_Lden_all_v2"),
        lambda config: config.update(metric="LAeq"),
        lambda config: config.update(tile_size_m=5000),
    ],
)
def test_scientific_identity_mismatch_leaves_outputs_untouched(monkeypatch, tmp_path, change):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    output_root = tmp_path / "outputs"
    manifest_path = tmp_path / "manifest.json"
    runner.run_batch(config, output_root, manifest_path, mask_path)
    before_manifest = manifest_path.read_bytes()

    stale_dir = output_root / "temporary_10m" / "stale-inputs"
    stale_dir.mkdir(parents=True)
    staged = output_root / "tiles" / ".r0000c0000.attempt-99.tif"
    staged.write_bytes(b"keep me")
    changed = copy.deepcopy(config)
    change(changed)
    with pytest.raises(ValueError, match="Existing outputs were left untouched"):
        runner.run_batch(changed, output_root, manifest_path, mask_path)
    assert len(calls) == 1
    assert manifest_path.read_bytes() == before_manifest
    assert stale_dir.exists()
    assert staged.read_bytes() == b"keep me"


def test_legacy_manifest_is_rejected_without_modification(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    manifest_path = tmp_path / "legacy.json"
    manifest_path.write_text(json.dumps({"tiles": {"old": {}}}), encoding="utf-8")
    before = manifest_path.read_bytes()
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    with pytest.raises(ValueError, match="legacy or unverified manifest"):
        runner.run_batch(config, tmp_path / "outputs", manifest_path, mask_path)
    assert manifest_path.read_bytes() == before
    assert not calls


def test_tampered_identity_payload_is_rejected_without_modification(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    manifest_path = tmp_path / "manifest.json"
    runner.run_batch(config, tmp_path / "outputs", manifest_path, mask_path)
    tampered = json.loads(manifest_path.read_text(encoding="utf-8"))
    tampered["build_identity"]["payload"]["metric"] = "LAeq"
    manifest_path.write_text(json.dumps(tampered, indent=2), encoding="utf-8")
    before = manifest_path.read_bytes()
    with pytest.raises(ValueError, match="inconsistent build identity"):
        runner.run_batch(config, tmp_path / "outputs", manifest_path, mask_path)
    assert manifest_path.read_bytes() == before
    assert len(calls) == 1


def test_incompatible_stored_tile_definition_is_rejected_before_cleanup(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    output_root = tmp_path / "outputs"
    manifest_path = tmp_path / "manifest.json"
    runner.run_batch(config, output_root, manifest_path, mask_path)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["tiles"]["r0000c0000"]["tile"]["bbox_epsg27700"][2] += 100.0
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    stale_dir = output_root / "temporary_10m" / "stale-inputs"
    stale_dir.mkdir(parents=True)
    with pytest.raises(ValueError, match="tile definition"):
        runner.run_batch(config, output_root, manifest_path, mask_path)
    assert stale_dir.exists()
    assert len(calls) == 1


def test_changed_mask_bytes_at_same_path_refuse_resume(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    _write_mask(mask_path)
    config = _identity_config(mask_path)
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    manifest_path = tmp_path / "manifest.json"
    runner.run_batch(config, tmp_path / "outputs", manifest_path, mask_path)
    with rasterio.open(mask_path, "r+") as dataset:
        dataset.write(np.array([[0] + [1] * 99] + [[1] * 100 for _ in range(99)], dtype="uint8"), 1)
    with pytest.raises(ValueError, match="Build identity mismatch"):
        runner.run_batch(config, tmp_path / "outputs", manifest_path, mask_path)
    assert len(calls) == 1


def test_identical_mask_bytes_at_different_path_do_not_change_identity(monkeypatch, tmp_path):
    mask_path = tmp_path / "mask.tif"
    copied_mask_path = tmp_path / "copied-mask.tif"
    _write_mask(mask_path)
    shutil.copyfile(mask_path, copied_mask_path)
    config = _identity_config(mask_path)
    calls = []
    monkeypatch.setattr(runner, "process_tile", _successful_fake_process(calls))
    output_root = tmp_path / "outputs"
    manifest_path = tmp_path / "manifest.json"
    runner.run_batch(config, output_root, manifest_path, mask_path)
    changed_path_config = copy.deepcopy(config)
    changed_path_config["england_mask_100m_path"] = str(copied_mask_path)
    second = runner.run_batch(changed_path_config, output_root, manifest_path, copied_mask_path)
    assert second["skipped_complete"] == 1
    assert len(calls) == 1
