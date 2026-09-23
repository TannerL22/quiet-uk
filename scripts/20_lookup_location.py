"""Look up one stored Quiet UK cell from BNG or WGS84 coordinates."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiet_uk.catalogue import TARGET_CRS, WGS84_CRS, lookup_location


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Read one qualified Quiet UK catalogue cell")
    parser.add_argument("catalogue_dir")
    parser.add_argument("tile_dir")
    parser.add_argument("land_mask")
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--bng", nargs=2, type=float, metavar=("EASTING", "NORTHING"))
    group.add_argument("--wgs84", nargs=2, type=float, metavar=("LONGITUDE", "LATITUDE"))
    parser.add_argument("--diagnostic", action="store_true", help="Expose raw stored values with an unqualified warning")
    args = parser.parse_args(argv)
    if args.bng is not None:
        result = lookup_location(
            args.catalogue_dir, args.tile_dir, args.bng[0], args.bng[1], TARGET_CRS,
            land_mask_path=args.land_mask, diagnostic=args.diagnostic,
        )
    else:
        result = lookup_location(
            args.catalogue_dir, args.tile_dir, args.wgs84[0], args.wgs84[1], WGS84_CRS,
            land_mask_path=args.land_mask, diagnostic=args.diagnostic,
        )
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
