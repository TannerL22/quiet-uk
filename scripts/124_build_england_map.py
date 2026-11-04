"""Build or verify the England map from the native 10 m acquisition.

The map holds display tiles and a record list only; point values are read from
the acquisition's archives. Use --preview to draw from an unfinished download.
"""
import argparse
import json
from pathlib import Path
import sys
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk import england_map


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--acquisition', type=Path, default=ROOT/'artifacts/england_acquisition_v1')
    parser.add_argument('--output', type=Path, default=ROOT/'artifacts/england_map_v1')
    parser.add_argument('--workers', type=int, default=6)
    parser.add_argument('--preview', action='store_true', help='Allow an unsealed, incomplete acquisition')
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    # PROJ warns that the OSTN15 grid is missing; the manifest records the operation used instead.
    warnings.filterwarnings('ignore', message='Best transformation is not available')
    if args.verify:
        result = england_map.verify(args.output)
    else:
        manifest = england_map.build(args.acquisition, args.output, workers=args.workers, allow_incomplete=args.preview)
        result = {k: manifest[k] for k in ('release_id', 'acquisition', 'tiles')}
        result['transformation'] = manifest['transformation']['description']
    print(json.dumps(result, indent=2))


if __name__ == '__main__':
    main()
