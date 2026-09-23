"""Generate a read-only Quiet UK coverage and evidence report."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiet_uk.dataset_report import generate_dataset_report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate a read-only Quiet UK dataset coverage and evidence report"
    )
    parser.add_argument("catalogue_dir")
    parser.add_argument("tile_dir")
    parser.add_argument("land_mask")
    parser.add_argument("manifest")
    parser.add_argument("reconciliation_report")
    parser.add_argument("config")
    parser.add_argument("output_dir")
    args = parser.parse_args(argv)
    result = generate_dataset_report(
        args.catalogue_dir,
        args.tile_dir,
        args.land_mask,
        args.manifest,
        args.reconciliation_report,
        args.config,
        args.output_dir,
    )
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
