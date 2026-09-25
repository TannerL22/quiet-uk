"""Plan, acquire/resume or verify the bounded 50 km tiled extraction canary."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.source_pilot import SourcePilot
from quiet_uk.tiled_canary import plan, acquire, verify


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/tiled_canary_v1')
    parser.add_argument('--reference', type=Path, default=ROOT/'artifacts/source_regions_v2')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--acquire', action='store_true')
    mode.add_argument('--verify', action='store_true')
    parser.add_argument('--max-new-rasters', type=int, default=225)
    args = parser.parse_args()
    if not 1 <= args.max_new_rasters <= 225:
        parser.error('Choose 1–225 new rasters per invocation')
    if not args.acquire and not args.verify:
        print(json.dumps(plan(), indent=2)); return
    reference = SourcePilot(args.reference)
    result = (acquire(args.output.resolve(), reference, max_new_rasters=args.max_new_rasters)
              if args.acquire else verify(args.output.resolve(), reference))
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
