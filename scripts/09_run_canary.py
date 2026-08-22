"""Run the geographically diverse canary through the production runner."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiet_uk.runner import plan_england_tiles, run_batch


ANCHORS = [
    ("central_london", 530000, 180000),
    ("heathrow_aviation", 505000, 175000),
    ("m25_m4_motorway", 500000, 185000),
    ("bristol_airport_40db", 355000, 165000),
    ("manchester_urban", 385000, 385000),
    ("birmingham_motorways", 410000, 285000),
    ("leeds_urban", 425000, 435000),
    ("east_anglia_rural", 600000, 260000),
    ("norfolk_coast", 635000, 335000),
    ("peak_district_expected_quiet", 410000, 365000),
    ("lake_district_expected_quiet", 335000, 505000),
    ("south_coast", 450000, 95000),
    ("southwest_rural", 255000, 105000),
    ("england_wales_boundary", 330000, 305000),
    ("england_scotland_boundary", 345000, 595000),
    ("newcastle_northeast", 420000, 570000),
]


def _tile_at(tiles, x, y):
    for tile in tiles:
        minx, miny, maxx, maxy = tile.bbox
        if minx <= x < maxx and miny <= y < maxy:
            return tile
    return None


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--resume-test", action="store_true")
    args = parser.parse_args()
    config = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
    mask_path = ROOT / config["england_mask_100m_path"]
    tiles = plan_england_tiles(config, mask_path)
    selected = []
    selection = []
    seen = set()
    for label, x, y in ANCHORS:
        tile = _tile_at(tiles, x, y)
        if tile is not None and tile.tile_id not in seen:
            seen.add(tile.tile_id)
            selected.append(tile)
            selection.append({"label": label, "anchor_epsg27700": [x, y], "tile": tile.to_dict()})
    if len(selected) < 12:
        raise SystemExit(f"Only {len(selected)} England-intersecting canary tiles selected; expected at least 12")

    output_root = ROOT / "data" / "processed" / "canary"
    manifest_path = output_root / "tile_status_manifest.json"
    selection_path = output_root / "canary_selection.json"
    selection_path.parent.mkdir(parents=True, exist_ok=True)
    selection_path.write_text(json.dumps({"anchors": selection}, indent=2), encoding="utf-8")
    before = None
    if args.resume_test and manifest_path.exists():
        before = json.loads(manifest_path.read_text(encoding="utf-8"))
        target = selected[0].tile_id
        before["tiles"][target]["status"] = "failed"
        before["tiles"][target]["last_error"] = "Safe resume-test simulation"
        manifest_path.write_text(json.dumps(before, indent=2), encoding="utf-8")

    result = run_batch(
        config, output_root, manifest_path, mask_path,
        tile_ids=[tile.tile_id for tile in selected],
        failed_only=args.resume_test,
    )
    if args.resume_test and before is not None:
        after = json.loads(manifest_path.read_text(encoding="utf-8"))
        changed = [
            tile_id for tile_id in before["tiles"]
            if after["tiles"].get(tile_id, {}).get("attempts", 0)
            > before["tiles"][tile_id].get("attempts", 0)
        ]
        (output_root / "resume_test.json").write_text(
            json.dumps({"rerun_tile_ids": changed, "expected_count": 1, "result": result}, indent=2),
            encoding="utf-8",
        )
    print(json.dumps({"selected_count": len(selected), "selection": selection, "result": result}, indent=2))


if __name__ == "__main__":
    main()
