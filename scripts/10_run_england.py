"""Production-safe resumable England tile runner.

The command intentionally does not default to a dry run: launch it only after
the canary and production-readiness note have been reviewed.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiet_uk.runner import run_batch


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--output-root", default="data/processed/england")
    parser.add_argument("--manifest", default="data/processed/england/tile_status_manifest.json")
    parser.add_argument("--failed-only", action="store_true")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--workers", type=int)
    parser.add_argument("--tile-id", action="append", dest="tile_ids")
    args = parser.parse_args()
    config = json.loads((ROOT / args.config).read_text(encoding="utf-8"))
    result = run_batch(
        config,
        ROOT / args.output_root,
        ROOT / args.manifest,
        ROOT / config["england_mask_100m_path"],
        tile_ids=args.tile_ids,
        failed_only=args.failed_only,
        limit=args.limit,
        workers=args.workers,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
