"""Acquire a small source-linked pilot, or verify it entirely offline."""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.source_pilot import SourcePilot, publish, SITES, acquisition_plan, compare_reference


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/source_pilot_v1')
    parser.add_argument('--verify', action='store_true', help='Verify saved evidence without network access')
    parser.add_argument('--reproduce', action='store_true', help='Also reproduce every display PNG from originals')
    parser.add_argument('--extent-km', type=int, choices=(2, 10), default=2,
                        help='Side length of each of four areas; new acquisitions only')
    parser.add_argument('--plan', action='store_true', help='Print the bounded acquisition recipe without networking')
    parser.add_argument('--compare-to', type=Path, help='Check every native cell in an earlier, smaller release')
    args = parser.parse_args()
    if args.reproduce and not args.verify:
        parser.error('--reproduce requires --verify')
    sites = {key: {**value, 'size_m': args.extent_km*1000} for key, value in SITES.items()}
    if args.plan:
        print(json.dumps(acquisition_plan(sites), indent=2))
        return
    if not args.verify:
        print('Published', publish(args.output, sites=sites)['release_id'])
    product = SourcePilot(args.output)
    print(json.dumps(product.verify(reproduce=args.reproduce), indent=2))
    if args.compare_to:
        print(json.dumps(compare_reference(product, SourcePilot(args.compare_to)), indent=2))


if __name__ == '__main__':
    main()
