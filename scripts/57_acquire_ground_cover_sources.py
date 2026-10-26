"""Acquire bounded original 2021 land-cover and historical barrier evidence.

Public reads only; no account creation, enquiries or noise predictions.
"""
import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk import generations
from quiet_uk.explorer import file_hash, json_bytes

BUCKET = 'https://esa-worldcover.s3.eu-central-1.amazonaws.com/'
TILE = 'N51W003'
OSM_BBOX = (51.4423, -.5518, 51.5573, -.3677)
DATES = ('2020-12-31T23:59:59Z', '2021-12-31T23:59:59Z')
MAX_REQUESTS, MAX_TOTAL = 24, 256 * 1024 * 1024


# Kept importable here for scripts that load this file by path.
verified, seal = generations.verified, generations.seal


def run(output):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Use a fresh workspace generation')
    parent = ROOT / 'artifacts/ground_barrier_audit_v1'
    verified(parent)
    parent_hash = file_hash(parent / 'inventory.json')
    output.mkdir(parents=True)
    shutil.copytree(ROOT / 'artifacts/ground_cover_discovery_v1/documentation', output / 'documentation')
    receipts, total = [], 0
    session = requests.Session()
    session.max_redirects = 5
    session.headers['User-Agent'] = 'Quiet-UK/0.2 (bounded historical input research; https://github.com/TannerL22/quiet-uk)'

    def fetch(name, url, params=None, cap=20 * 1024 * 1024, required=False):
        nonlocal total
        if len(receipts) >= MAX_REQUESTS:
            raise ValueError('Request budget exceeded')
        path = output / name
        receipt = dict(file=name, method='GET', source_url=url, params=params,
                       started_utc=datetime.now(timezone.utc).isoformat())
        size, sha = 0, hashlib.sha256()
        try:
            with session.get(url, params=params, stream=True, timeout=(15, 75)) as response:
                receipt.update(response_url=response.url, status=response.status_code,
                    headers={k: response.headers.get(k) for k in ('Content-Type', 'Content-Length', 'Last-Modified', 'ETag')})
                declared = response.headers.get('Content-Length')
                if declared and int(declared) > cap:
                    raise ValueError('Declared response exceeds per-file budget')
                with path.open('wb') as stream:
                    for chunk in response.iter_content(65536):
                        # Count every received chunk, including one triggering the cap.
                        total += len(chunk)
                        if size + len(chunk) > cap or total > MAX_TOTAL:
                            raise ValueError('Cumulative/response byte budget exceeded')
                        stream.write(chunk)
                        sha.update(chunk)
                        size += len(chunk)
                response.raise_for_status()
                if declared and not response.headers.get('Content-Encoding') and int(declared) != size:
                    raise ValueError('Truncated response')
            receipt['state'] = 'downloaded'
            return path
        except (requests.RequestException, ValueError) as error:
            receipt.update(state='unavailable', error=str(error))
            print(f'{name}: unavailable ({error})', flush=True)
            if required:
                raise
            return None
        finally:
            receipt.update(completed_utc=datetime.now(timezone.utc).isoformat(),
                           retained_bytes=size, sha256=sha.hexdigest())
            receipts.append(receipt)
            (output / 'receipts.json').write_bytes(json_bytes(receipts))

    grid = fetch('worldcover-grid.geojson', BUCKET + 'v200/2021/esa_worldcover_grid.geojson', required=True)
    tiles = json.loads(grid.read_bytes())['features']
    matching = [f for f in tiles if f['properties']['ll_tile'] == TILE]
    if len(matching) != 1:
        raise ValueError('Expected one official source tile')
    map_name = f'ESA_WorldCover_10m_2021_v200_{TILE}_Map.tif'
    fetch(map_name, BUCKET + 'v200/2021/map/' + map_name, cap=128 * 1024 * 1024, required=True)
    print('Original WorldCover 2021 tile retained', flush=True)
    for report in ('PUM', 'PVR'):
        fetch(f'WorldCover_{report}_V2.0.pdf', BUCKET + f'v200/2021/docs/WorldCover_{report}_V2.0.pdf')
    listing = fetch('worldcover-inputquality-listing.xml', BUCKET,
                    {'list-type': '2', 'prefix': 'v200/2021/inputquality/', 'max-keys': 1000})
    quality_file, quality_state = None, 'listing_unavailable'
    if listing:
        document = ET.fromstring(listing.read_bytes())
        namespace = {'s': 'http://s3.amazonaws.com/doc/2006-03-01/'}
        keys = [e.text for e in document.findall('s:Contents/s:Key', namespace)]
        if document.findtext('s:IsTruncated', namespaces=namespace) != 'false':
            quality_state = 'listing_truncated; no absence inference'
        else:
            selected = [k for k in keys if f'_{TILE}_InputQuality.tif' in k]
            quality_state = 'tile_not_listed_in_public_prefix'
            if len(selected) == 1:
                p = fetch(Path(selected[0]).name, BUCKET + selected[0])
                quality_file = p.name if p else None
                quality_state = 'downloaded' if p else 'unavailable'

    osm_sources = []
    bbox = ','.join(str(n) for n in OSM_BBOX)
    for year, date in zip((2020, 2021), DATES):
        query = (f'[out:json][timeout:45][maxsize:16777216][date:"{date}"];'
                 f'(way["barrier"]({bbox});way["wall"="noise_barrier"]({bbox});'
                 f'way["man_made"="embankment"]({bbox}););out meta geom;')
        (output / f'osm-{year}-query.overpassql').write_text(query, encoding='utf-8')
        selected = None
        for attempt, endpoint in enumerate(('https://overpass.kumi.systems/api/interpreter',
                                           'https://overpass-api.de/api/interpreter'), 1):
            path = fetch(f'osm-{year}-attempt-{attempt}.json', endpoint, {'data': query})
            if path is None:
                continue
            try:
                data = json.loads(path.read_bytes())
                elements = data['elements']
                if data.get('remark') or len(elements) > 10000:
                    raise ValueError('Incomplete/oversized OSM response')
                if any(e['type'] != 'way' or not e.get('geometry') or e['timestamp'] > date for e in elements):
                    raise ValueError('Unexpected/nonhistorical way')
                selected = path.name
                print(f'Historical {year} candidate ways: {len(elements)}', flush=True)
                break
            except (KeyError, ValueError) as error:
                receipts[-1]['qualification_error'] = str(error)
                (output / 'receipts.json').write_bytes(json_bytes(receipts))
        osm_sources.append(dict(snapshot_utc=date, response_file=selected, query_file=f'osm-{year}-query.overpassql',
                                state='retained' if selected else 'unavailable'))

    nh = 'https://www.arcgis.com/sharing/rest/'
    fetch('national-highways-hub-item.json', nh + 'content/items/87d87933f0a840acb65c90fb421f4aff', {'f': 'json'})
    searches = []
    for scope, query in [('organisation', 'orgid:mZXeBXkkZpekxjXT (noise OR barrier)'),
                         ('content-group', 'group:47848125b6934f998f57d462889c2d72 (noise OR barrier)')]:
        name = f'national-highways-{scope}-search.json'
        p = fetch(name, nh + 'search', {'f': 'json', 'q': query, 'num': 100})
        searches.append(dict(scope=scope, file=name if p else None, query=query))
    manifest = dict(schema_version=1, reference_year=2021, study=json.loads((parent / 'report.json').read_bytes())['study'],
        parent_inventory_sha256={parent.name: parent_hash}, map_file=map_name, tile=TILE,
        quality_file=quality_file, quality_state=quality_state, osm_sources=osm_sources,
        osm_bbox_wgs84_south_west_north_east=OSM_BBOX,
        osm_scope='Ways with any barrier tag, explicit wall=noise_barrier, or man_made=embankment. Nodes/relations and unmapped barriers are not enumerated.',
        historical_limit='Two edit snapshots do not establish physical presence, uninterrupted 2021 stability, sealing, material absorption or height accuracy.',
        national_highways_searches=searches, request_count=len(receipts), download_bytes=total,
        budgets=dict(requests=MAX_REQUESTS, total_bytes=MAX_TOTAL, map_bytes=128 * 1024 * 1024, other_response_bytes=20 * 1024 * 1024),
        licences={'ESA_WorldCover': 'Creative Commons Attribution 4.0', 'OSM': 'Open Database Licence 1.0'},
        attributions=['ESA WorldCover 10 m 2021 v200, Zanaga et al. (2022), doi:10.5281/zenodo.7254221.',
            '© ESA WorldCover project 2021 / Contains modified Copernicus Sentinel data (2021) processed by ESA WorldCover consortium.',
            '© OpenStreetMap contributors; https://www.openstreetmap.org/copyright'],
        national_highways_status='Public catalogue metadata only, no qualified asset data.',
        ukceh_status='Native 2021 raster access redirects to EIDC login; not acquired or commercially qualified.',
        real_area_noise_predictions=False)
    code = Path(__file__)
    shutil.copyfile(code, output / code.name)
    manifest['acquisition_code_sha256'] = file_hash(code)
    (output / 'manifest.json').write_bytes(json_bytes(manifest))
    verified(parent)
    if file_hash(parent / 'inventory.json') != parent_hash:
        raise ValueError('Parent changed during acquisition')
    identity = seal(output, 'ground-cover-sources-')
    print(json.dumps(dict(generation_id=identity, bytes=total, requests=len(receipts),
                         quality_state=quality_state, osm_sources=osm_sources)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'artifacts/ground_cover_sources_v1'))
    run(parser.parse_args().output)
