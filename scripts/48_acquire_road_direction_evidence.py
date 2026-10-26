"""Bounded directional AADF additions and a historical OSM one-way snapshot.

No traffic assumptions, routing messages, model fitting or noise predictions.
Exact responses, query time, source timestamps, hashes and failures are retained.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import sys

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import file_hash, json_bytes

OSM_DATE = '2021-12-31T23:59:59Z'
OSM_BBOX = (51.4423, -.5518, 51.5573, -.3677)  # 250 m halo expansion; WGS84 envelope.
MAX_REQUESTS, MAX_TOTAL, MAX_RESPONSE = 6, 40 * 1024 * 1024, 18 * 1024 * 1024


def run(output, reuse_source=None):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Use a fresh workspace artifact directory')
    audit = ROOT / 'artifacts/road_input_audit_v1'
    temporal = ROOT / 'artifacts/road_temporal_audit_v2'
    parents = {}
    for directory in (audit, temporal):
        inv = json.loads((directory / 'inventory.json').read_bytes())
        for name, digest in inv['files'].items():
            if file_hash(directory / name) != digest:
                raise ValueError('Parent checksum mismatch')
        parents[directory.name] = file_hash(directory / 'inventory.json')
    temporal_report = json.loads((temporal / 'report.json').read_bytes())
    selected = temporal_report['summary']['direct_matcher_donor_ids_outside_local_temporal_selection']
    if not 0 < len(selected) <= 50 or len(set(selected)) != len(selected):
        raise ValueError('Unexpected donor extension')
    output.mkdir(parents=True)
    docs = ROOT / 'artifacts/road_direction_documentation_v1'
    shutil.copytree(docs, output / 'documentation')
    receipts, total = [], 0
    reused = None
    if reuse_source is not None:
        reuse_source = Path(reuse_source).resolve()
        if not reuse_source.is_relative_to(ROOT / 'artifacts'):
            raise ValueError('Reuse only workspace source generations')
        old = json.loads((reuse_source / 'inventory.json').read_bytes())
        for name, digest in old['files'].items():
            if file_hash(reuse_source / name) != digest:
                raise ValueError('Reused source checksum mismatch')
        old_manifest = json.loads((reuse_source / 'manifest.json').read_bytes())
        if old_manifest['additional_donor_ids'] != selected or old_manifest['parent_inventory_sha256'] != parents:
            raise ValueError('Reused source belongs to another selection')
        reused = {'generation_id': old['generation_id'],
                  'inventory_sha256': file_hash(reuse_source / 'inventory.json')}
        shutil.copyfile(reuse_source / 'receipts.json', output / 'reused-source-receipts.json')

    def fetch(name, url, params):
        nonlocal total
        if len(receipts) >= MAX_REQUESTS:
            raise ValueError('Request budget exceeded')
        started = datetime.now(timezone.utc).isoformat()
        path = output / name
        sha, size = hashlib.sha256(), 0
        receipt = {'file': name, 'method': 'GET', 'source_url': url, 'params': params,
                   'started_utc': started}
        try:
            with requests.get(url, params=params, stream=True, timeout=(15, 65), headers={
                'User-Agent': 'Quiet-UK-research/0.2 (bounded historical carriageway audit; https://github.com/TannerL22/quiet-uk)',
                'Accept': 'application/json'}) as response:
                receipt.update(url=response.request.url, response_url=response.url, status=response.status_code,
                               headers={k: response.headers.get(k) for k in ('Content-Type', 'Last-Modified', 'ETag')})
                with path.open('wb') as stream:
                    for chunk in response.iter_content(65536):
                        size += len(chunk)
                        if size > MAX_RESPONSE or total + size > MAX_TOTAL:
                            raise ValueError('Response byte budget exceeded')
                        stream.write(chunk); sha.update(chunk)
                response.raise_for_status()
            return json.loads(path.read_bytes())
        except Exception as error:
            receipt['error'] = str(error)
            raise
        finally:
            total += size
            receipt.update(completed_utc=datetime.now(timezone.utc).isoformat(),
                           response_bytes=size, sha256=sha.hexdigest())
            receipts.append(receipt)
            (output / 'receipts.json').write_bytes(json_bytes(receipts))

    page, rows, seen = 1, [], set()
    while True:
        name = f'additional-aadf-directions-{page:02d}.json'
        if reuse_source is not None:
            shutil.copyfile(reuse_source / name, output / name)
            payload = json.loads((output / name).read_bytes())
        else:
            payload = fetch(name,
                'https://roadtraffic.dft.gov.uk/api/average-annual-daily-flow-by-direction',
                {'filter[count_point_id]': ','.join(selected), 'filter[year]': 2021,
                 'page[size]': 100, 'page[number]': page, 'sort': 'id'})
        if payload['current_page'] != page or payload['per_page'] != 100 or payload['total'] > 2 * len(selected):
            raise ValueError('Unexpected API pagination')
        for row in payload['data']:
            if str(row['count_point_id']) not in selected or row['year'] != 2021 or row['id'] in seen:
                raise ValueError('Unexpected directional row')
            seen.add(row['id']); rows.append(row)
        if page == payload['last_page']:
            if len(rows) != payload['total']:
                raise ValueError('Incomplete directional source')
            break
        page += 1
        if page > 2:
            raise ValueError('Directional page budget exceeded')
    print(f'Additional official directional records: {len(rows)} at {len(selected)} donor IDs', flush=True)
    bbox = ','.join(str(n) for n in OSM_BBOX)
    query = (f'[out:json][timeout:45][maxsize:16777216][date:"{OSM_DATE}"];'
             f'(way["highway"]["oneway"]({bbox});'
             f'way["highway"~"^(motorway|trunk|primary|secondary|tertiary)$"]({bbox}););out meta geom;')
    (output / 'osm-query.overpassql').write_text(query, encoding='utf-8')
    osm_file, failures = None, []
    for attempt, endpoint in enumerate(('https://overpass.kumi.systems/api/interpreter',
                                        'https://overpass-api.de/api/interpreter'), 1):
        name = f'osm-historical-attempt-{attempt}.json'
        try:
            data = fetch(name, endpoint, {'data': query})
            if data.get('remark') or not 0 < len(data.get('elements', [])) <= 10000:
                raise ValueError('Incomplete, unavailable or oversized OSM response')
            if any(e.get('type') != 'way' or not e.get('geometry') or e.get('timestamp', '') > OSM_DATE
                   for e in data['elements']):
                raise ValueError('Unexpected/nonhistorical OSM way')
            osm_file = name
            print(f'Historical OSM ways retained: {len(data["elements"])}', flush=True)
            break
        except (requests.RequestException, ValueError) as error:
            failures.append({'endpoint': endpoint, 'file': name, 'error': str(error)})
            print(f'Historical OSM attempt {attempt} unavailable: {error}', flush=True)
    manifest = {'schema_version': 1, 'reference_year': 2021,
                'additional_donor_ids': selected, 'additional_directional_records': len(rows),
                'reused_directional_source': reused,
                'parent_inventory_sha256': parents, 'osm_response_file': osm_file,
                'osm_requested_snapshot_utc': OSM_DATE, 'osm_bbox_wgs84_south_west_north_east': OSM_BBOX,
                'osm_query_scope': 'explicit one-way highways plus major through-road highway classes',
                'osm_failures': failures, 'request_count': len(receipts), 'download_bytes': total,
                'acquisition_code_sha256': file_hash(Path(__file__)),
                'licences': {'DfT': 'Open Government Licence v3.0', 'OS': 'Open Government Licence v3.0',
                             'OSM': 'Open Database Licence 1.0'},
                'attributions': ['Source: Department for Transport road traffic statistics, 2021.',
                                 'Contains OS data © Crown copyright and database right 2026.',
                                 '© OpenStreetMap contributors; https://www.openstreetmap.org/copyright'],
                'historical_limit': 'Year-end OSM snapshot and edit timestamps do not prove travel restrictions were unchanged throughout 2021.'}
    shutil.copyfile(Path(__file__), output / Path(__file__).name)
    (output / 'manifest.json').write_bytes(json_bytes(manifest))
    for directory in (audit, temporal):
        if file_hash(directory / 'inventory.json') != parents[directory.name]:
            raise ValueError('Parent changed during acquisition')
    files = {str(p.relative_to(output)).replace('\\', '/'): file_hash(p)
             for p in sorted(output.rglob('*')) if p.is_file()}
    inventory = {'generation_id': 'road-direction-sources-' + hashlib.sha256(json_bytes(files)).hexdigest()[:20],
                 'files': files}
    (output / 'inventory.json').write_bytes(json_bytes(inventory))
    print(json.dumps({'generation_id': inventory['generation_id'], 'manifest': manifest}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'artifacts/road_direction_sources_v1'))
    parser.add_argument('--reuse-source', default=None)
    args = parser.parse_args()
    run(args.output, args.reuse_source)
