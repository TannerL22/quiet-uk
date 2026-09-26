"""Build or replay the offline tiled map derivative. Never contacts a provider."""
import argparse
import json
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.tiled_release import publish
from quiet_uk.source_pilot import SourcePilot

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--canary', type=Path, default=ROOT/'artifacts/tiled_canary_v1')
    parser.add_argument('--regional', type=Path, default=ROOT/'artifacts/source_regions_v2')
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/tiled_map_v2')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    if not args.verify:
        publish(args.canary, args.regional, args.output)
    print(json.dumps(SourcePilot(args.output).verify(reproduce=True), indent=2))

if __name__ == '__main__':
    main()
