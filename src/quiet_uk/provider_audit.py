"""Bounded WCS extraction audit. Agreement never certifies acoustic semantics.

Network acquisition is explicit; saved reports can be reproduced offline. The
two protocol paths discover their own identifiers, including broken adverts.
"""
from __future__ import annotations

from collections import Counter
import json
from pathlib import Path
import re
import shutil
import xml.etree.ElementTree as ET

import numpy as np
import rasterio
import requests
from rasterio.windows import Window, bounds as window_bounds

from .source_pilot import (PROVIDERS, METRICS, capture, coverage_ids,
                          select_coverage, native_grid, site_bounds, file_hash,
                          json_bytes)

VERSIONS = ('1.0.0', '2.0.1')
EXPLAINER = 'https://www.gov.uk/government/publications/strategic-noise-mapping-2022/explaining-the-2022-noise-maps'


def exception_text(path):
    try:
        root = ET.fromstring(Path(path).read_bytes())
    except ET.ParseError:
        return None
    if root.tag.rsplit('}', 1)[-1] in ('ServiceExceptionReport', 'ExceptionReport'):
        return ' '.join(' '.join(root.itertext()).split())
    return None


def description(path):
    error = exception_text(path)
    if error:
        return {'status': 'service_exception', 'message': error}
    try:
        xml = Path(path).read_bytes()
        grid = native_grid(xml)
        root = ET.fromstring(xml)
        envelopes = [e for e in root.iter() if e.tag.rsplit('}', 1)[-1] == 'Envelope']
        corners = [list(map(float, e.text.split())) for e in envelopes[0]
                   if e.tag.rsplit('}', 1)[-1] in ('pos', 'lowerCorner', 'upperCorner')]
        if len(corners) != 2:
            raise ValueError('Expected one pair of envelope corners')
        return {'status': 'available', 'grid': grid, 'envelope': corners[0]+corners[1],
                'nil_values': [{'value': e.text, 'reason': e.get('reason')}
                               for e in root.iter() if e.tag.rsplit('}', 1)[-1] == 'nilValue'],
                'service_units': [e.get('code') for e in root.iter()
                                  if e.tag.rsplit('}', 1)[-1] == 'uom']}
    except (ValueError, ET.ParseError, IndexError) as exc:
        return {'status': 'invalid_description', 'message': str(exc)}


def request_params(version, identifier, bounds):
    """Independent protocol recipes; no identifier rewriting or resampling."""
    w, s, e, n = bounds
    shape = ((n-s)/10, (e-w)/10)
    if not np.isfinite(bounds).all() or any(x != 20 for x in shape):
        raise ValueError('Audit requests must be 20 by 20 native cells')
    common = [('service', 'WCS'), ('version', version), ('request', 'GetCoverage')]
    if version == '1.0.0':
        return common + [('coverage', identifier), ('bbox', ','.join(map(str, bounds))),
                         ('crs', 'EPSG:27700'), ('response_crs', 'EPSG:27700'),
                         ('width', '20'), ('height', '20'), ('format', 'GeoTIFF')]
    if version != '2.0.1':
        raise ValueError('Unsupported audit protocol')
    return common + [('coverageId', identifier), ('format', 'image/tiff'),
                     ('subset', f'E({w},{e})'), ('subset', f'N({s},{n})')]


def read_raster(path):
    error = exception_text(path)
    if error:
        return {'status': 'service_exception', 'message': error}, None, None
    try:
        with rasterio.open(path) as ds:
            if ds.count != 1:
                raise ValueError('Expected single band')
            a, mask = ds.read(1), ds.read_masks(1) == 0
            positive = a[(~mask) & (a > 0)]
            info = {'status': 'raster', 'shape': list(a.shape), 'crs': str(ds.crs),
                    'transform': list(ds.transform), 'bounds': list(ds.bounds),
                    'dtype': str(a.dtype), 'nodata': ds.nodata,
                    'masked_cells': int(mask.sum()),
                    'zero_cells': int(((a == 0) & ~mask).sum()),
                    'positive_cells': int(positive.size),
                    'positive_min': float(positive.min()) if positive.size else None,
                    'positive_max': float(positive.max()) if positive.size else None}
        if not np.isfinite(a).all() or info['nodata'] is None or not np.isfinite(info['nodata']):
            raise ValueError('Expected finite samples and finite TIFF nodata')
        return info, a, mask
    except rasterio.errors.RasterioError:
        # GDAL messages include absolute local paths, which are not portable
        # evidence and must not make offline report reproduction path-dependent.
        return {'status': 'invalid_raster', 'message': 'Unreadable raster response'}, None, None
    except ValueError as exc:
        return {'status': 'invalid_raster', 'message': str(exc)}, None, None


def compare_rasters(left, right, expected_bounds):
    """Fail closed on failed requests, shifted/clipped grids, masks or values."""
    li, la, lm = read_raster(left)
    ri, ra, rm = read_raster(right)
    result = {'left': li, 'right': ri}
    if la is None or ra is None:
        return {**result, 'status': 'unavailable'}
    w, s, e, n = expected_bounds
    expected = {'crs': 'EPSG:27700', 'shape': [20, 20],
                'transform': [10., 0., w, 0., -10., n, 0., 0., 1.],
                'bounds': list(expected_bounds)}
    result['request_grid_matches'] = [all(info[k] == v for k, v in expected.items())
                                      for info in (li, ri)]
    if not all(result['request_grid_matches']):
        return {**result, 'status': 'grid_mismatch'}
    result['dtype_equal'] = li['dtype'] == ri['dtype']
    result['nodata_equal'] = li['nodata'] == ri['nodata']
    result['mask_difference_cells'] = int((lm != rm).sum())
    result['raw_difference_cells'] = int((la != ra).sum())
    valid = ~lm & ~rm
    result['valid_difference_cells'] = int(((la != ra) & valid).sum())
    result['max_abs_valid_difference'] = float(np.abs(la[valid].astype('float64')-ra[valid]).max()) if valid.any() else None
    equal = (result['dtype_equal'] and result['nodata_equal'] and
             result['mask_difference_cells'] == result['raw_difference_cells'] == 0)
    return {**result, 'status': 'exact_match' if equal else 'encoding_or_value_mismatch'}


def discover(root):
    for source, provider in PROVIDERS.items():
        endpoint = f'https://environment.data.gov.uk/spatialdata/{provider["service"]}/wcs'
        capture(root, f'evidence/{source}.html', f'https://environment.data.gov.uk/dataset/{provider["id"]}')
        for version in VERSIONS:
            caps = capture(root, f'evidence/{source}-{version}-capabilities.xml', endpoint,
                           {'service': 'WCS', 'request': 'GetCapabilities', 'version': version}, required=False)
            if ET.fromstring(caps.read_bytes()).get('version') != version:
                raise ValueError('Capabilities negotiated a different protocol')
            for metric in METRICS:
                identifier = select_coverage(coverage_ids(caps.read_bytes()), source, metric)
                capture(root, f'evidence/{source}-{metric}-{version}-description.xml', endpoint,
                        {'service': 'WCS', 'version': version, 'request': 'DescribeCoverage',
                         'coverage' if version == '1.0.0' else 'coverageId': identifier}, required=False)
    capture(root, 'evidence/explaining-2022.html', EXPLAINER)


def make_plan(root, reference):
    """Select 200 m stress windows from pinned originals, without interpolation."""
    root, reference = Path(root), Path(reference)
    m = json.loads((reference/'manifest.json').read_text('utf-8'))
    cases = []
    for source in PROVIDERS:
        site = {'road': 'heathrow', 'rail': 'didcot', 'aircraft': 'heathrow'}[source]
        for metric in METRICS:
            record = next(r for r in m['records'] if r['source'] == source and r['metric'] == metric and r['site'] == site)
            original = reference/record['path']
            if file_hash(original) != record['sha256']:
                raise ValueError('Reference raster checksum changed')
            with rasterio.open(original) as ds:
                a, mask = ds.read(1), ds.read_masks(1) == 0
                windows = []
                for row in range(0, ds.height-19, 20):
                    for col in range(0, ds.width-19, 20):
                        tile, missing = a[row:row+20, col:col+20], mask[row:row+20, col:col+20]
                        values = tile[(~missing) & (tile > 0)]
                        spread = float(np.ptp(values)) if values.size else -1
                        transition = min(values.size, 400-values.size)
                        windows.append((row, col, spread, transition, values.size))
                picks = [('reported', max(windows, key=lambda x: x[2])),
                         ('transition', max(windows, key=lambda x: x[3]))]
                if metric == 'Lden' and min(x[4] for x in windows) == 0:
                    picks.append(('unreported', min(windows, key=lambda x: x[4])))
                for kind, (row, col, spread, transition, count) in picks:
                    if kind == 'transition' and transition == 0:
                        raise ValueError('Reference has no mixed reported/unreported window')
                    if kind == 'unreported' and count != 0:
                        raise ValueError('Reference has no fully unreported window')
                    case_id = f'{source}-{metric}-{kind}'
                    window = Window(col, row, 20, 20)
                    path = root/f'reference/{case_id}.tif'
                    path.parent.mkdir(parents=True, exist_ok=True)
                    profile = ds.profile.copy()
                    profile.update(width=20, height=20, transform=ds.window_transform(window))
                    with rasterio.open(path, 'w', **profile) as out:
                        out.write(ds.read(window=window))
                        out.write_mask(ds.dataset_mask(window=window))
                    cases.append({'id': case_id, 'source': source, 'metric': metric,
                                  'kind': kind, 'bounds': list(window_bounds(window, ds.transform)),
                                  'reference': str(path.relative_to(root)).replace('\\', '/'),
                                  'parent_record': record['id'], 'parent_sha256': record['sha256'],
                                  'parent_window_col_row_width_height': [col, row, 20, 20]})
        # Geographic stress probes are NOT model-domain membership assertions.
        desc = description(root/f'evidence/{source}-Lden-2.0.1-description.xml')
        if desc['status'] != 'available':
            raise ValueError('Missing native grid for audit recipe')
        cases.append({'id': f'{source}-Lden-chilterns', 'source': source, 'metric': 'Lden',
                      'kind': 'geographic_probe_not_domain_proof',
                      'bounds': site_bounds({'center': [-0.969, 51.641], 'size_m': 200}, desc['grid'])})
        if source != 'aircraft':
            cases.append({'id': f'{source}-Lden-cardiff', 'source': source, 'metric': 'Lden',
                          'kind': 'geographic_probe_not_domain_proof',
                          'bounds': site_bounds({'center': [-3.18, 51.48], 'size_m': 200}, desc['grid'])})
        w, s, e, n = desc['envelope']
        cases.append({'id': f'{source}-Lden-envelope-edge', 'source': source, 'metric': 'Lden',
                      'kind': 'straddles_advertised_rectangle',
                      'bounds': [w-100, n-200, w+100, n]})
    plan = {'schema_version': 1, 'reference_release_id': m['release_id'],
            'reference_manifest_sha256': file_hash(reference/'manifest.json'),
            'selection': '20x20 non-overlapping windows; maximum positive range, maximum mixed-cell balance, and zero positive count; ties in row/column order',
            'cases': cases, 'max_coverage_requests': len(cases)*2,
            'max_requested_cells': len(cases)*2*400}
    (root/'plan.json').write_bytes(json_bytes(plan))
    return plan


def analyse(root):
    root = Path(root)
    plan = json.loads((root/'plan.json').read_text('utf-8'))
    descriptions = {f'{s}-{m}-{v}': description(root/f'evidence/{s}-{m}-{v}-description.xml')
                    for s in PROVIDERS for m in METRICS for v in VERSIONS}
    results = []
    for case in plan['cases']:
        paths = [root/f'raw/{case["id"]}-{v}.tif' for v in VERSIONS]
        comparison = compare_rasters(*paths, case['bounds'])
        http_status = [json.loads(Path(str(p)+'.http.json').read_text('utf-8'))['status'] for p in paths]
        comparison['http_status'] = http_status
        if http_status != [200, 200]:
            comparison['status'] = 'unavailable'
        references = {}
        if case.get('reference'):
            for version, path, status in zip(VERSIONS, paths, http_status):
                check = compare_rasters(path, root/case['reference'], case['bounds'])
                if status != 200:
                    check['status'] = 'unavailable'
                references[version] = check
        results.append({'id': case['id'], 'kind': case['kind'], 'bounds': case['bounds'],
                        'protocol_comparison': comparison, 'reference_comparisons': references})
    return {'schema_version': 1, 'quietness_bounds_authorized': False,
            'scope': 'Small native windows from two WCS interfaces to the same provider; not independent acoustic validation or proof of zero/domain semantics.',
            'protocol_counts': dict(Counter(r['protocol_comparison']['status'] for r in results)),
            'reference_counts': dict(Counter(c['status'] for r in results for c in r['reference_comparisons'].values())),
            'descriptions': descriptions, 'results': results}


def validate_plan(plan):
    cases = plan['cases']
    if not 1 <= len(cases) <= 30 or len({c['id'] for c in cases}) != len(cases):
        raise ValueError('Invalid audit case count or duplicate identifiers')
    for case in cases:
        if (case['source'] not in PROVIDERS or case['metric'] not in METRICS or
                not re.fullmatch(r'[a-z]+-L(?:den|day|night)-[a-z-]+', case['id'])):
            raise ValueError('Invalid audit case identity')
        request_params('2.0.1', 'validate', case['bounds'])


def acquire(root, reference):
    root = Path(root)
    if (root/'manifest.json').exists():
        raise FileExistsError('Sealed audit exists; use offline verification or a new destination')
    root.mkdir(parents=True, exist_ok=True)
    discover(root)
    plan = (json.loads((root/'plan.json').read_text('utf-8')) if (root/'plan.json').exists()
            else make_plan(root, reference))
    validate_plan(plan)
    for case in plan['cases']:
        source, metric = case['source'], case['metric']
        endpoint = f'https://environment.data.gov.uk/spatialdata/{PROVIDERS[source]["service"]}/wcs'
        for version in VERSIONS:
            ids = coverage_ids((root/f'evidence/{source}-{version}-capabilities.xml').read_bytes())
            identifier = select_coverage(ids, source, metric)
            capture(root, f'raw/{case["id"]}-{version}.tif', endpoint,
                    request_params(version, identifier, case['bounds']), required=False)
        print('Captured', case['id'], flush=True)
    report = analyse(root)
    (root/'report.json').write_bytes(json_bytes(report))
    (root/'construction').mkdir(exist_ok=True)
    shutil.copyfile(__file__, root/'construction/provider_audit.py')
    files = {str(p.relative_to(root)).replace('\\', '/'): file_hash(p)
             for p in sorted(root.rglob('*')) if p.is_file() and p.name != 'manifest.json'}
    (root/'manifest.json').write_bytes(json_bytes({'schema_version': 1, 'files': files}))
    return verify(root)


def verify(root):
    root = Path(root)
    manifest = json.loads((root/'manifest.json').read_text('utf-8'))
    for name, digest in manifest['files'].items():
        path = (root/name).resolve()
        if not path.is_relative_to(root.resolve()) or file_hash(path) != digest:
            raise ValueError('Audit evidence checksum mismatch: '+name)
    for path in root.rglob('*.http.json'):
        record = json.loads(path.read_text('utf-8'))
        response = Path(str(path)[:-len('.http.json')])
        if file_hash(response) != record['sha256'] or response.stat().st_size != record['bytes']:
            raise ValueError('HTTP response checksum mismatch')
    plan = json.loads((root/'plan.json').read_text('utf-8'))
    validate_plan(plan)
    for case in plan['cases']:
        source, metric = case['source'], case['metric']
        endpoint = f'https://environment.data.gov.uk/spatialdata/{PROVIDERS[source]["service"]}/wcs'
        for version in VERSIONS:
            caps = root/f'evidence/{source}-{version}-capabilities.xml'
            if ET.fromstring(caps.read_bytes()).get('version') != version:
                raise ValueError('Capabilities protocol mismatch')
            identifier = select_coverage(coverage_ids(caps.read_bytes()), source, metric)
            url = requests.Request('GET', endpoint, params=request_params(version, identifier, case['bounds'])).prepare().url
            record = json.loads((root/f'raw/{case["id"]}-{version}.tif.http.json').read_text('utf-8'))
            if record['request_url'] != url:
                raise ValueError('Response request differs from pinned audit recipe')
    report = analyse(root)
    if report != json.loads((root/'report.json').read_text('utf-8')):
        raise ValueError('Saved audit report does not reproduce')
    return {'verified_files': len(manifest['files']), 'report_reproduced': True,
            'protocol_counts': report['protocol_counts'], 'reference_counts': report['reference_counts'],
            'quietness_bounds_authorized': False}
