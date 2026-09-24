"""Build the derived map once, then serve the local Quiet UK explorer."""
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
    parser.add_argument('--geocoder', default='https://nominatim.openstreetmap.org/search')
    regional = ROOT/'artifacts/source_regions_v1'
    interpreted = ROOT/'artifacts/source_regions_v2'
    if (interpreted/'manifest.json').exists():
        regional = interpreted
    parser.add_argument('--pilot', type=Path, default=regional if (regional/'manifest.json').exists() else ROOT/'artifacts/source_pilot_v1')
    args = parser.parse_args()
    if not args.display.exists():
        print('Preparing verified map display assets. Scientific source files remain unchanged.', flush=True)
        manifest = publish_display(args.catalogue, args.tiles, args.mask, args.display)
        print('Published', manifest['release_id'], flush=True)
    explorer = Explorer(args.display, args.catalogue, args.tiles, args.mask)
    if args.build_only:
        print('Display verified:', explorer.manifest['release_id'])
        return
    pilot = SourcePilot(args.pilot) if (args.pilot/'manifest.json').exists() else None
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
