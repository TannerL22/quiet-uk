"""Download and prepare the official ONS December 2024 England land mask."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quiet_uk.land_mask import ONS_FEATURE_SERVICE, prepare_england_mask


def main() -> None:
    config_path = ROOT / "config.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))
    output_dir = ROOT / "data" / "processed" / "england_mask"
    metadata = prepare_england_mask(
        output_dir,
        source_url=config.get("ons_england_boundary_url", ONS_FEATURE_SERVICE),
        target_crs=config.get("crs", "EPSG:27700"),
        timeout=int(config.get("wcs_request_timeout_s", 180)),
    )
    print(json.dumps(metadata, indent=2))


if __name__ == "__main__":
    main()
