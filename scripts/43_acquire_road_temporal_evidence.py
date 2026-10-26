"""Acquire bounded official 2021 temporal traffic evidence for the frozen study.

Public GET requests only. Saves exact response bytes, requests and checksums.
No annual time-of-day allocation, speed assignment or noise prediction occurs.
"""
import argparse
import csv
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import file_hash, json_bytes

TABLES = {
    'tra0307.ods': 'https://assets.publishing.service.gov.uk/media/6a0b7e78c510c3913d8267ce/tra0307-traffic-distribution-by-time-of-day.ods',
    'tra0308.ods': 'https://assets.publishing.service.gov.uk/media/6a0b7ea0fcae986635db9142/tra0308-traffic-distribution-by-time-of-day-and-selected-vehicle-type.ods',
    'spe0102.ods': 'https://assets.publishing.service.gov.uk/media/685a8814db207fc18744d5ed/spe0102.ods',
}
MAX_RESPONSE_BYTES = 5 * 1024 * 1024
MAX_TOTAL_BYTES = 32 * 1024 * 1024
MAX_REQUESTS = 30


def run(output):
    output = Path(output).resolve()
    if output.exists() or output.is_relative_to(ROOT / 'data'):
        raise ValueError('Use a new artifact directory outside frozen data')
    parent = ROOT / 'artifacts/road_input_audit_v1'
    inventory = json.loads((parent / 'inventory.json').read_bytes())
    for name, digest in inventory['files'].items():
        if file_hash(parent / name) != digest:
            raise ValueError('Parent audit checksum mismatch')
    with (parent / 'local_2021_count_points.csv').open(encoding='utf-8', newline='') as stream:
        points = list(csv.DictReader(stream))
    ids = {str(row['count_point_id']) for row in points}
    if len(ids) != len(points) or not 0 < len(ids) <= 100:
        raise ValueError('Expected at most 100 distinct audited local points')
    code_hash = file_hash(Path(__file__))
    payloads, receipts, total_bytes = {}, [], 0
    session = requests.Session()
    session.headers['Accept'] = 'application/json'

    def fetch(name, url, params=None):
        nonlocal total_bytes
        if len(receipts) >= MAX_REQUESTS:
            raise ValueError('Request budget exceeded')
        started = datetime.now(timezone.utc).isoformat()
        with session.get(url, params=params, timeout=(10, 30), stream=True) as response:
            response.raise_for_status()
            parsed = urlparse(response.url)
            if parsed.scheme != 'https' or parsed.hostname not in ('roadtraffic.dft.gov.uk', 'assets.publishing.service.gov.uk'):
                raise ValueError('Unexpected response origin')
            chunks, size = [], 0
            for chunk in response.iter_content(65536):
                size += len(chunk)
                if size > MAX_RESPONSE_BYTES or total_bytes + size > MAX_TOTAL_BYTES:
                    raise ValueError('Response byte budget exceeded')
                chunks.append(chunk)
            body = b''.join(chunks)
            total_bytes += size
            receipts.append({
                'file': name, 'method': 'GET', 'url': response.request.url,
                'response_url': response.url, 'status': response.status_code,
                'started_utc': started, 'completed_utc': datetime.now(timezone.utc).isoformat(),
                'response_bytes': size, 'sha256': hashlib.sha256(body).hexdigest(),
                'headers': {key: response.headers.get(key) for key in
                            ('Content-Type', 'Content-Length', 'Last-Modified', 'ETag')},
            })
            payloads[name] = body
            return body

    counts = {}
    for endpoint in ('raw-counts', 'average-annual-daily-flow-by-direction'):
        page, expected_total, seen_ids, received = 1, None, set(), 0
        while True:
            body = fetch(f'{endpoint}-page-{page:02d}.json',
                         f'https://roadtraffic.dft.gov.uk/api/{endpoint}',
                         {'filter[count_point_id]': ','.join(sorted(ids, key=int)),
                          'filter[year]': 2021, 'page[size]': 100,
                          'page[number]': page, 'sort': 'id'})
            data = json.loads(body)
            if data.get('current_page') != page or data.get('per_page') != 100:
                raise ValueError('Unexpected API pagination')
            if expected_total is None:
                expected_total = data['total']
            if data['total'] != expected_total or not 0 <= data['total'] <= 10000:
                raise ValueError('Unstable or unbounded API result')
            for row in data['data']:
                if str(row['count_point_id']) not in ids or row['year'] != 2021:
                    raise ValueError('API ignored exact point/year filtering')
                if row['id'] in seen_ids:
                    raise ValueError('Duplicate API record across pages')
                seen_ids.add(row['id'])
                if endpoint == 'raw-counts' and not str(row['count_date']).startswith('2021-'):
                    raise ValueError('Raw count date differs from reference year')
            received += len(data['data'])
            if page == data['last_page']:
                if received != expected_total:
                    raise ValueError('Incomplete API pagination')
                break
            if not data['data'] or page > data['last_page']:
                raise ValueError('Invalid API page progression')
            page += 1
        counts[endpoint] = {'rows': received, 'pages': page}
    for name, url in TABLES.items():
        body = fetch(name, url)
        if not body.startswith(b'PK'):
            raise ValueError('Expected original ODS archive')
    for name, digest in inventory['files'].items():
        if file_hash(parent / name) != digest:
            raise ValueError('Parent audit changed during acquisition')
    if file_hash(Path(__file__)) != code_hash:
        raise ValueError('Acquisition code changed during run')
    output.mkdir(parents=True)
    for name, body in payloads.items():
        (output / name).write_bytes(body)
    manifest = {
        'schema_version': 1, 'status': 'official_sources_acquired_not_temporally_allocated',
        'reference_year': 2021, 'parent_audit_id': inventory['audit_id'],
        'parent_inventory_sha256': file_hash(parent / 'inventory.json'),
        'selected_count_point_ids': sorted(ids, key=int), 'api_results': counts,
        'request_budget': MAX_REQUESTS, 'byte_budget': MAX_TOTAL_BYTES,
        'requests': receipts, 'total_response_bytes': total_bytes,
        'code_sha256': {'scripts/43_acquire_road_temporal_evidence.py': code_hash},
        'licence': {'name': 'Open Government Licence v3.0',
                    'url': 'https://www.nationalarchives.gov.uk/doc/open-government-licence/version/3/',
                    'attribution': 'Source: Department for Transport road traffic and vehicle speed statistics.'},
        'limitations': [
            'Comma-separated count point filtering is verified against every returned record; the API docs describe this parameter as an integer.',
            'Live national table versions must be checked for an exact 2021 row; no latest-year substitution is permitted.',
            'A sequence of API responses is not a transactional provider snapshot; duplicate IDs, filters and total counts are checked.',
            'Acquisition does not establish annual hourly flows, local speeds or acoustic validity.',
        ],
    }
    (output / 'manifest.json').write_bytes(json_bytes(manifest))
    files = {path.name: file_hash(path) for path in sorted(output.iterdir())}
    identity = 'road-temporal-sources-' + files['manifest.json'][:20]
    (output / 'inventory.json').write_bytes(json_bytes({'source_id': identity, 'files': files}))
    print(json.dumps({'source_id': identity, 'api_results': counts,
                      'requests': len(receipts), 'bytes': total_bytes, 'output': str(output)}, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/road_temporal_sources_v1')
    run(parser.parse_args().output)

