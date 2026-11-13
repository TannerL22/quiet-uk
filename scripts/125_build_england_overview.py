"""Build or verify the England map overview: area-averaged zoomed-out tiles and quiet-area hexagons.

Reads the archived Defra rasters through a verified England map release and
writes a separate display product beside it. The map and its tiles are not changed.
"""
import argparse
import json
from pathlib import Path
import sys
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk import england_acquisition, england_overview


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--map', type=Path, default=ROOT/'artifacts/england_map_v1')
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/england_overview_v1')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    warnings.filterwarnings('ignore', message='Best transformation is not available')
    england_acquisition.keep_awake(True)  # A long build: stop idle sleep (lid close still sleeps).
    try:
        if args.verify:
            result = england_overview.verify(args.output, args.map, workers=args.workers)
        else:
            manifest = england_overview.build(args.map, args.output, workers=args.workers)
            result = {k: manifest[k] for k in ('release_id', 'map', 'tiles', 'hexes')}
    finally:
        england_acquisition.keep_awake(False)
    print(json.dumps(result, indent=2), flush=True)


if __name__ == '__main__':
    main()
