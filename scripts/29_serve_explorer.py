"""Serve regional evidence independently, with an optional historical overview."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import Explorer, publish_display
from quiet_uk.explorer_server import ExplorerServer, PlaceSearch
from quiet_uk.source_pilot import SourcePilot


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port', type=int, default=8766)
    parser.add_argument('--catalogue', type=Path, default=ROOT/'artifacts/england_catalogue_v3')
    parser.add_argument('--tiles', type=Path, default=ROOT/'data/processed/england/tiles')
    parser.add_argument('--mask', type=Path, default=ROOT/'data/processed/england_mask/england_100m_mask.tif')
    parser.add_argument('--display', type=Path, default=ROOT/'artifacts/explorer_display_v4')
    parser.add_argument('--build-only', action='store_true')
    parser.add_argument('--regional-only', action='store_true', help='Use only the regional bundle; never read or build historical data')
    parser.add_argument('--geocoder', default='https://nominatim.openstreetmap.org/search')
    regional = ROOT/'artifacts/source_regions_v1'
    interpreted = ROOT/'artifacts/source_regions_v2'
    if (interpreted/'manifest.json').exists():
        regional = interpreted
    parser.add_argument('--pilot', type=Path, default=regional if (regional/'manifest.json').exists() else ROOT/'artifacts/source_pilot_v1')
    args = parser.parse_args()
    if args.regional_only and args.build_only:
        parser.error('--regional-only cannot be combined with --build-only')
    if args.build_only and not args.display.exists():
        print('Preparing verified map display assets. Scientific source files remain unchanged.', flush=True)
        manifest = publish_display(args.catalogue, args.tiles, args.mask, args.display)
        print('Published', manifest['release_id'], flush=True)
    if args.build_only:
        explorer = Explorer(args.display, args.catalogue, args.tiles, args.mask)
        print('Display verified:', explorer.manifest['release_id'])
        return
    pilot = SourcePilot(args.pilot) if (args.pilot/'manifest.json').exists() else None
    if args.regional_only and pilot is None:
        parser.error('Regional evidence is missing. Supply --pilot PATH to an extracted regional bundle.')
    explorer = None
    if not args.regional_only and all(p.exists() for p in (args.display/'dataset.json', args.catalogue/'catalogue.sqlite3', args.tiles, args.mask)):
        explorer = Explorer(args.display, args.catalogue, args.tiles, args.mask)
    if pilot is None and explorer is None:
        parser.error('No local release found. Supply --pilot PATH to an extracted regional bundle. Historical data is built only with --build-only.')
    print('Historical overview: available at /overview' if explorer else 'Regional mode: historical overview not installed or disabled.', flush=True)
    server = ExplorerServer(args.port, explorer, ROOT/'explorer', PlaceSearch(args.geocoder), pilot=pilot)
    print(f'Quiet UK explorer: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    main()
