"""Acquire bounded original donor counts and historical Heathrow weather.

Public GETs, exact 2021 data, immutable source generations. ISD is retained as
a reproducible historical product; its GHCNh successor is documented separately.
"""
import argparse
from datetime import datetime, timezone
import importlib.util
import json
from pathlib import Path
import shutil
import sys
from urllib.parse import urlparse

import requests

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk.explorer import file_hash, json_bytes

spec = importlib.util.spec_from_file_location('inventory_helper', ROOT / 'scripts/57_acquire_ground_cover_sources.py')
helper = importlib.util.module_from_spec(spec)
spec.loader.exec_module(helper)

URLS = {
    'heathrow-2021.gz': 'https://www.ncei.noaa.gov/pub/data/noaa/2021/037720-99999-2021.gz',
    'isd-history.csv': 'https://www.ncei.noaa.gov/pub/data/noaa/isd-history.csv',
    'isd-format-document.pdf': 'https://www.ncei.noaa.gov/pub/data/noaa/isd-format-document.pdf',
    'isd-product.html': 'https://www.ncei.noaa.gov/products/land-based-station/integrated-surface-database',
    'ghcnh-successor.html': 'https://www.ncei.noaa.gov/products/global-historical-climatology-network-hourly',
    'humidity-formula.html': 'https://www.weather.gov/epz/wxcalc_dewpoint',
    'humidity-formula.pdf': 'https://www.weather.gov/media/epz/wxcalc/rhTdFromWetBulb.pdf',
    'dft-about.html': 'https://roadtraffic.dft.gov.uk/about',
}


def run(output):
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT / 'artifacts'):
        raise ValueError('Use a fresh workspace artifact generation')
    parents = {n: ROOT / 'artifacts' / n for n in
               ('road_direction_inputs_v2', 'road_temporal_audit_v2', 'road_emission_assumptions_v2')}
    for p in parents.values(): helper.verified(p)
    hashes = {n: file_hash(p / 'inventory.json') for n, p in parents.items()}
    temporal = json.loads((parents['road_temporal_audit_v2'] / 'local_survey_day_inputs.json').read_bytes())
    examined = {p['count_point_id'] for p in temporal['points']}
    partitions = json.loads((parents['road_direction_inputs_v2'] / 'donor_partitions.json').read_bytes())['points']
    ids = sorted({p['count_point_id'] for p in partitions} - examined, key=int)
    if len(ids) != 17: raise ValueError('Unexpected unexamined donor selection')
    output.mkdir(parents=True)
    receipts, total = [], 0
    session = requests.Session()
    session.max_redirects = 4
    session.headers['User-Agent'] = 'Quiet-UK/0.2 (bounded historical input research)'

    def fetch(name, url, params=None, required=True):
        nonlocal total
        if len(receipts) >= 20: raise ValueError('Request budget exceeded')
        receipt = dict(file=name, method='GET', source_url=url, params=params,
                       started_utc=datetime.now(timezone.utc).isoformat())
        body = bytearray()
        try:
            with session.get(url, params=params, stream=True, timeout=(15, 40)) as response:
                receipt.update(response_url=response.url, status=response.status_code,
                               headers={k: response.headers.get(k) for k in
                                        ('Content-Type', 'Content-Length', 'Last-Modified', 'ETag')})
                origin = urlparse(response.url)
                if (origin.scheme != 'https' or origin.hostname not in
                        ('www.ncei.noaa.gov', 'www.weather.gov', 'roadtraffic.dft.gov.uk')):
                    raise ValueError('Unexpected response origin')
                response.raise_for_status()
                for chunk in response.iter_content(65536):
                    total += len(chunk)
                    if len(body) + len(chunk) > 12 * 1024**2 or total > 40 * 1024**2:
                        raise ValueError('Byte budget exceeded')
                    body.extend(chunk)
                if name.endswith('.gz') and not body.startswith(b'\x1f\x8b'):
                    raise ValueError('Expected original gzip')
                if name.endswith('.pdf') and not body.startswith(b'%PDF'):
                    raise ValueError('Expected original PDF')
            (output / name).write_bytes(body)
            receipt.update(state='downloaded', response_bytes=len(body), sha256=file_hash(output / name))
            return bytes(body)
        except (requests.RequestException, ValueError) as error:
            receipt.update(state='unavailable', error=str(error), received_bytes=len(body))
            if required: raise
            return None
        finally:
            receipt['completed_utc'] = datetime.now(timezone.utc).isoformat()
            receipts.append(receipt)
            (output / 'receipts.json').write_bytes(json_bytes(receipts))
            print(f'{name}: {receipt["state"]}', flush=True)

    expected, seen, received, page = None, set(), 0, 1
    while True:
        body = fetch(f'raw-counts-page-{page:02d}.json', 'https://roadtraffic.dft.gov.uk/api/raw-counts',
                     {'filter[count_point_id]': ','.join(ids), 'filter[year]': 2021,
                      'page[size]': 100, 'page[number]': page, 'sort': 'id'})
        data = json.loads(body)
        if data['current_page'] != page or data['per_page'] != 100:
            raise ValueError('Unexpected pagination')
        if expected is None: expected = data['total']
        if data['total'] != expected or expected > 1000: raise ValueError('Unstable/unbounded result')
        for row in data['data']:
            if str(row['count_point_id']) not in ids or row['year'] != 2021 or row['id'] in seen:
                raise ValueError('Wrong year/point or duplicate ID')
            if not row['count_date'].startswith('2021-'): raise ValueError('Wrong survey year')
            seen.add(row['id'])
        received += len(data['data'])
        if page == data['last_page']:
            if received != expected: raise ValueError('Incomplete pages')
            break
        if not data['data'] or page > data['last_page']: raise ValueError('Invalid page progression')
        page += 1
    for name, url in URLS.items(): fetch(name, url, required=not name.endswith('.html'))
    shutil.copyfile(__file__, output / Path(__file__).name)
    manifest = dict(reference_year=2021, selected_additional_donors=ids, raw_count_rows=received,
                    source_station='037720-99999', weather_product='Historical ISD; superseded by GHCNh, not a live service',
                    source_urls=URLS, parent_inventory_sha256=hashes,
                    total_response_bytes=total, requests=len(receipts), request_budget=20,
                    max_response_bytes=12*1024**2, max_total_bytes=40*1024**2,
                    licences={'DfT': 'Open Government Licence v3.0; Source: Department for Transport, 2021.',
                              'NOAA': 'Publicly accessible NOAA/NCEI observations; original contributing station/source identity retained.'},
                    acquisition_code_sha256=file_hash(__file__))
    (output / 'manifest.json').write_bytes(json_bytes(manifest))
    for n, p in parents.items():
        helper.verified(p)
        assert file_hash(p / 'inventory.json') == hashes[n]
    print(dict(generation_id=helper.seal(output, 'road-operating-evidence-'), rows=received, bytes=total), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=ROOT / 'artifacts/road_operating_evidence_v1')
    run(parser.parse_args().output)
