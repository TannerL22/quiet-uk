"""Plan, seed, download/resume or verify native 10 m rasters for all of England.

Seeding reuses the sealed Oxford–London acquisition. Downloading resumes from the
records already saved and keeps Windows from sleeping while idle; create a file
named STOP in the output folder to stop cleanly after the current raster.
"""
import argparse
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk import england_acquisition as england
from quiet_uk.source_pilot import SourcePilot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/england_acquisition_v1')
    parser.add_argument('--parent', type=Path, default=ROOT/'artifacts/oxford_london_acquisition_v1')
    parser.add_argument('--reference', type=Path, default=ROOT/'artifacts/source_regions_v2')
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument('--seed', action='store_true', help='Verify Oxford–London and archive its rasters as reused')
    mode.add_argument('--acquire', action='store_true')
    mode.add_argument('--verify', action='store_true')
    parser.add_argument('--max-new-rasters', type=int)
    args = parser.parse_args()
    if args.seed:
        result = england.seed(args.parent, args.output, SourcePilot(args.reference))
    elif args.acquire:
        if sys.platform == 'win32':
            # ES_CONTINUOUS | ES_SYSTEM_REQUIRED: no idle sleep while this process runs; the screen may still turn off.
            import ctypes
            ctypes.windll.kernel32.SetThreadExecutionState(0x80000000 | 0x00000001)
        result = england.acquire(args.output, max_new_rasters=args.max_new_rasters)
    elif args.verify:
        result = england.verify(args.output, log=print)
    else:
        parent = json.loads((args.parent/'plan.json').read_text('utf-8'))
        recipe = england.plan(ROOT/england.MASK, parent)
        result = {k: v for k, v in recipe.items() if k != 'tiles'}
        result['reused_tiles'] = sum('parent_tile' in t for t in recipe['tiles'])
    print(json.dumps({k: v for k, v in result.items() if k != 'files'}, indent=2, default=str))


if __name__ == '__main__':
    main()
