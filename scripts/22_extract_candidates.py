#!/usr/bin/env python3
"""Extract bounded road/rail candidate components from the Quiet UK catalogue."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiet_uk.candidates import extract_candidates  # noqa: E402


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Screen a bounded EPSG:27700 100 m window for road/rail candidate components."
    )
    parser.add_argument("catalogue_dir", type=Path, help="Reviewed catalogue directory")
    parser.add_argument("tile_root", type=Path, help="Directory containing the catalogue tiles")
    parser.add_argument("land_mask", type=Path, help="Authoritative England land-mask GeoTIFF")
    parser.add_argument("output_dir", type=Path, help="New empty directory for candidate outputs")
    parser.add_argument(
        "--bng",
        nargs=4,
        type=float,
        required=True,
        metavar=("WEST", "SOUTH", "EAST", "NORTH"),
        help="Exact 100 m-aligned BNG bbox: west south east north",
    )
    parser.add_argument(
        "--threshold-db",
        type=float,
        required=True,
        help="Inclusive road_rail_upper_db threshold in dB",
    )
    parser.add_argument(
        "--minimum-component-cells",
        type=int,
        required=True,
        help="Minimum 4-neighbour component size to retain",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    report = extract_candidates(
        args.catalogue_dir,
        args.tile_root,
        args.land_mask,
        args.bng,
        args.threshold_db,
        args.minimum_component_cells,
        args.output_dir,
    )
    print(json.dumps({
        "run_id": report["run_id"],
        "retained_component_count": report["summary"]["retained_component_count"],
        "retained_component_cells": report["summary"]["retained_component_cells"],
        "output_paths": report["output_paths"],
    }, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
