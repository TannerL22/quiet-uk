"""Build or verify the separately qualified recorded-sound context release."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from quiet_uk.independent_observations import IndependentObservations, build_release

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument("--source", type=Path, default=ROOT / "artifacts/independent_evidence_v1/source")
parser.add_argument("--output", type=Path, default=ROOT / "artifacts/recorded_sound_v3")
parser.add_argument("--verify", action="store_true")
args = parser.parse_args()
manifest = IndependentObservations(args.output).manifest if args.verify else build_release(args.source, args.output)
print(json.dumps({"release_id": manifest["release_id"], "recordings": manifest["qa"]["accepted_recordings"],
                  "sites": manifest["qa"]["site_count"], "excluded_groups": len(manifest["qa"]["excluded_groups"])}, indent=2))
