"""Serve regional evidence independently, with an optional historical overview."""
import argparse
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import Explorer, publish_display
from quiet_uk.explorer_server import ExplorerServer, PlaceSearch
from quiet_uk.england_outline import DEFAULT_PATH as OUTLINE_PATH
from quiet_uk.place_names import DEFAULT_INDEX as PLACES_PATH
from quiet_uk.serving_snapshot import serving_environment


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
    observed = ROOT/'artifacts/recorded_sound_v3'
    parser.add_argument('--observations', type=Path, default=observed if (observed/'manifest.json').exists() else None,
                        help='Optional qualified recorded sound release, separate from annual model grids')
    regional = ROOT/'artifacts/source_regions_v1'
    interpreted = ROOT/'artifacts/source_regions_v2'
    if (interpreted/'manifest.json').exists():
        regional = interpreted
    tiled = ROOT/'artifacts/tiled_map_v2'
    if (tiled/'manifest.json').exists():
        regional = tiled
    expanded = ROOT/'artifacts/oxford_london_map_v1'
    if (expanded/'manifest.json').exists():
        regional = expanded
    parser.add_argument('--pilot', type=Path, default=regional if (regional/'manifest.json').exists() else ROOT/'artifacts/source_pilot_v1')
    england = next((path for path in (ROOT/'artifacts/england_map_v1', ROOT/'artifacts/england_map_preview')
                    if (path/'manifest.json').exists()), None)
    parser.add_argument('--england', type=Path, default=england, help='England map release (default: newest installed)')
    overview = ROOT/'artifacts/england_overview_v1'
    parser.add_argument('--england-overview', type=Path, default=overview if (overview/'manifest.json').exists() else None,
                        help='Averaged zoomed-out tiles and hexagons for the England map (default: installed overview)')
    outline = ROOT/OUTLINE_PATH
    parser.add_argument('--england-outline', type=Path, default=outline if outline.exists() else None,
                        help='ONS country boundary used to leave no-data outside England undrawn (default: installed file)')
    places = ROOT/PLACES_PATH
    parser.add_argument('--place-names', type=Path, default=places if (places/'manifest.json').exists() else None,
                        help='Local OS Open Names index for suggestions as you type (default: installed index)')
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
    # The server verifies bytes while copying its private snapshot. Avoid a
    # separate complete source hash pass before that same verified copy.
    pilot = args.pilot if (args.pilot/'manifest.json').exists() else None
    if args.regional_only and pilot is None:
        parser.error('Regional evidence is missing. Supply --pilot PATH to an extracted regional bundle.')
    explorer = None
    if not args.regional_only and all(p.exists() for p in (args.display/'dataset.json', args.catalogue/'catalogue.sqlite3', args.tiles, args.mask)):
        explorer = Explorer(args.display, args.catalogue, args.tiles, args.mask)
    if pilot is None and explorer is None and args.england is None:
        parser.error('No local release found. Supply --pilot PATH to an extracted regional bundle. Historical data is built only with --build-only.')
    if args.england is not None and not args.regional_only:
        print('England map:', args.england.name, flush=True)
        print('England overview:', args.england_overview.name if args.england_overview else 'not installed (exact tiles at every zoom)', flush=True)
        print('England boundary:', args.england_outline.name if args.england_outline else 'not installed (tiles drawn as stored)', flush=True)
        print('Place names:', args.place_names.name if args.place_names else 'not installed (search on Enter only)', flush=True)
    print('Historical overview: available at /overview' if explorer else 'Regional mode: historical overview not installed or disabled.', flush=True)
    server = ExplorerServer(args.port, explorer, ROOT/'explorer', PlaceSearch(args.geocoder), pilot=pilot,
                            observations=args.observations, england=None if args.regional_only else args.england,
                            england_overview=None if args.regional_only else args.england_overview,
                            england_outline=None if args.regional_only else args.england_outline,
                            place_names=None if args.regional_only else args.place_names)
    print(f'Quiet UK explorer: http://127.0.0.1:{server.server_port}', flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == '__main__':
    with serving_environment():
        main()
