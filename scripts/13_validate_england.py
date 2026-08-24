"""Run reproducible, windowed QA over the completed England tile set."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from quiet_uk.national_qa import run_national_qa


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", default="config.json")
    parser.add_argument("--output-root", default="data/processed/england")
    parser.add_argument("--manifest", default="data/processed/england/tile_status_manifest.json")
    parser.add_argument("--qa-root", default="data/processed/england/qa")
    parser.add_argument("--expected-tiles", type=int, default=1498)
    args = parser.parse_args()
    config = json.loads((ROOT / args.config).read_text(encoding="utf-8"))
    config["england_mask_100m_path"] = str(ROOT / config["england_mask_100m_path"])
    config["england_mask_metadata_path"] = str(ROOT / config["england_mask_metadata_path"])
    summary = run_national_qa(
        config,
        ROOT / args.output_root,
        ROOT / args.manifest,
        ROOT / args.qa_root,
        expected_tile_count=args.expected_tiles,
    )
    print(json.dumps(summary, indent=2))
    if not all(summary["checks"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
