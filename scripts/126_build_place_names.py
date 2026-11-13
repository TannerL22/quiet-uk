"""Build the place-name index for the England map's search box from OS Open Names.

Reads the pinned CSV download (checked by size and SHA-256) and writes a separate
SQLite index beside the map. Positions are converted with the England map's own
pinned transformation. The map is not changed. See notes/ENGLAND_MAP.md, "Search".
"""
import argparse
import json
from pathlib import Path
import sys
import warnings

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk import england_map, place_names


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=ROOT/place_names.DEFAULT_SOURCE)
    parser.add_argument('--map', type=Path, default=ROOT/'artifacts/england_map_v1')
    parser.add_argument('--output', type=Path, default=ROOT/place_names.DEFAULT_INDEX)
    parser.add_argument('--check', action='store_true', help='Only check an installed index against its manifest')
    args = parser.parse_args()
    warnings.filterwarnings('ignore', message='Best transformation is not available')
    if args.check:
        index = place_names.PlaceNames(args.output)
        print(json.dumps({'release_id': index.release_id, 'rows': index.manifest['rows']}, indent=2), flush=True)
        index.close()
        return
    info = england_map.read_manifest(args.map)['transformation']
    transformer = england_map.pinned_transformer(info)
    manifest = place_names.build(args.source, args.output, transformer, info)
    print(json.dumps({k: manifest[k] for k in ('release_id', 'rows', 'by_kind', 'index', 'duplicate_postcodes')}, indent=2), flush=True)


if __name__ == '__main__':
    main()
