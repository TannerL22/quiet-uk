"""Build a read-only SQLite/JSON catalogue for existing Quiet UK tiles."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiet_uk.catalogue import build_catalogue


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Build a validated Quiet UK tile catalogue without processing or downloads")
    parser.add_argument("tile_dir")
    parser.add_argument("manifest")
    parser.add_argument("reconciliation_report")
    parser.add_argument("land_mask")
    parser.add_argument("output_dir")
    parser.add_argument("--dataset-label", default="Quiet UK England historical four-band dataset")
    parser.add_argument("--reference-metadata")
    parser.add_argument(
        "--config",
        help="Optional production config supplying declared source bounds and frozen semantic thresholds",
    )
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)
    result = build_catalogue(
        args.tile_dir,
        args.manifest,
        args.reconciliation_report,
        args.land_mask,
        args.output_dir,
        dataset_label=args.dataset_label,
        reference_metadata_path=args.reference_metadata,
        config_path=args.config,
        overwrite=args.overwrite,
    )
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
