"""Bounded, release-pinned place comparisons from verified native cells."""
import csv
import io
import math

from .catalogue import CatalogueIntegrityError
from .evidence_semantics import difference_bounds


def validate_places(places, sites):
    if not isinstance(places, list) or not 1 <= len(places) <= 3:
        raise ValueError('Choose one to three places')
    cleaned, seen = [], set()
    for place in places:
        if not isinstance(place, dict) or set(place) != {'site', 'longitude', 'latitude', 'label'}:
            raise ValueError('Each place needs site, longitude, latitude and label')
        site, lon, lat, label = (place[k] for k in ('site', 'longitude', 'latitude', 'label'))
        if not isinstance(site, str) or site not in sites:
            raise ValueError('Unknown comparison area')
        if any(isinstance(x, bool) or not isinstance(x, (int, float)) or not math.isfinite(x) for x in (lon, lat)) or not (-180 <= lon <= 180 and -90 <= lat <= 90):
            raise ValueError('Comparison coordinates must be finite longitude/latitude in range')
        if not isinstance(label, str) or not 1 <= len(label.strip()) <= 60 or any(ord(c) < 32 or ord(c) == 127 for c in label):
            raise ValueError('Place names must contain 1–60 characters without control characters')
        key = (site, lon, lat)
        if key in seen:
            raise ValueError('This point is already in the comparison')
        seen.add(key)
        cleaned.append({**place, 'label': label.strip()})
    return cleaned


def compare_places(pilot, places, release_id):
    if release_id != pilot.manifest['release_id']:
        raise ValueError('Comparison release changed; choose places from the current release')
    places = validate_places(places, pilot.manifest['sites'])
    locations = [{'label': p['label'], **pilot.lookup(p['site'], p['longitude'], p['latitude'])} for p in places]
    # A comparison must never silently mix a stale point with the served release.
    if any(p['release_id'] != release_id for p in locations):
        raise CatalogueIntegrityError('Comparison contains mixed releases')
    rows = []
    for source in pilot.manifest['products']:
        product = pilot.manifest['products'][source]
        for metric in pilot.manifest['metrics']:
            observations = [next(o for o in p['observations'] if o['source'] == source and o['metric'] == metric) for p in locations]
            base = observations[0]
            differences = []
            for o in observations[1:]:
                delta, same_cell, bounds = None, None, None
                if base['status'] not in ('reported_model_value', 'verified_below_cutoff') or o['status'] not in ('reported_model_value', 'verified_below_cutoff'):
                    status = 'unreported_or_outside'
                elif not base['reference_period'] or not o['reference_period']:
                    status = 'reference_period_unspecified'
                elif base['reference_period'] != o['reference_period'] or base['coverage_id'] != o['coverage_id'] or base['units'] != o['units']:
                    status = 'incompatible_evidence'
                elif base['status'] == 'verified_below_cutoff' or o['status'] == 'verified_below_cutoff':
                    bounds = difference_bounds(base, o)
                    status = 'comparable_model_bounds' if bounds else 'bounds_do_not_rank'
                else:
                    status = 'comparable_model_values'
                    delta = o['value_db'] - base['value_db']
                    # Geometry also recognises shared cells in overlapping areas.
                    same_cell = o['cell'] == base['cell']
                differences.append({'difference_from_first_db': delta, 'status': status, 'same_native_cell': same_cell, 'difference_bounds_db': bounds})
            rows.append({'source': source, 'metric': metric, 'units': 'dB(A)',
                         'reference_period': product['reference_period'],
                         'reference_period_status': product['reference_period_status'],
                         'observations': observations, 'differences_from_first': differences})
    return {'schema_version': 2 if pilot.manifest['schema_version'] >= 2 else 1, 'release_id': release_id, 'places': locations, 'rows': rows,
            'spatial_resolution_m': pilot.manifest['spatial_resolution_m'],
            'receiver_height_m': pilot.manifest['receiver_height_m'],
            'missing_value_policy': pilot.manifest['missing_value_policy'],
            'uncertainty': pilot.manifest['uncertainty'], 'unavailable': pilot.manifest['unavailable'],
            'products': pilot.manifest['products'],
            **({'evidence_contract': pilot.manifest['evidence_contract'], 'parent_release_id': pilot.manifest['parent_release_id']}
               if pilot.manifest['schema_version'] >= 2 else
               {'reporting_cutoff_db': pilot.manifest['reporting_cutoff_db'], 'aircraft_cutoff_note': pilot.manifest['aircraft_cutoff_note']}),
            'licence': pilot.manifest['licence'], 'licence_url': pilot.manifest['licence_url'],
            'attribution': pilot.manifest['attribution'],
            'interpretation': 'Differences are place minus first place, within one source and indicator with a declared matching reference period. They are model differences, not measured change, a combined sound level, or a quietness ranking. Unreported and outside values stay null. Full cell precision is retained.'}


def comparison_csv(result):
    """One exact observation per row; empty numeric values always carry a status."""
    output = io.StringIO(newline='')
    fields = ['release_id', 'place', 'site', 'longitude', 'latitude', 'source', 'metric',
              'value_db', 'raw_value', 'status', 'units', 'reference_period_start', 'reference_period_end',
              'reference_period_status', 'coverage_id', 'source_sha256', 'http_record', 'row', 'column',
              'cell_geojson', 'spatial_resolution_m', 'receiver_height_m', 'reporting_cutoff_db',
              'difference_from_first_db', 'difference_status', 'same_native_cell',
              'missing_value_policy', 'uncertainty', 'aircraft_cutoff_note', 'licence_url', 'attribution']
    if result['schema_version'] == 2:
        fields.remove('aircraft_cutoff_note')
        fields += ['parent_release_id', 'evidence_contract_version', 'raw_encoding', 'bounds_json',
                   'provider_minimum_reporting_level_db', 'reporting_cutoff_status', 'threshold_evidence_json',
                   'observed_min_reported_db', 'observed_min_scope_json', 'period_evidence_json', 'interpretation_evidence_json', 'difference_bounds_json']
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    import json
    for index, place in enumerate(result['places']):
        # Prevent a user-supplied name being interpreted as a spreadsheet formula.
        label = place['label']
        if label.lstrip().startswith(('=', '+', '-', '@')):
            label = "'" + label
        for row in result['rows']:
            o = row['observations'][index]
            period = o['reference_period'] or {}
            difference = row['differences_from_first'][index-1] if index else {'status': 'baseline'}
            writer.writerow({
                **{k: result[k] for k in ('release_id', 'spatial_resolution_m', 'receiver_height_m', 'missing_value_policy', 'uncertainty', 'licence_url', 'attribution')},
                **({'aircraft_cutoff_note': result['aircraft_cutoff_note']} if result['schema_version'] == 1 else {
                    'parent_release_id': result['parent_release_id'], 'evidence_contract_version': o['contract_version'],
                    **{k: o[k] for k in ('raw_encoding', 'provider_minimum_reporting_level_db', 'reporting_cutoff_status', 'observed_min_reported_db')},
                    **{k+'_json': json.dumps(o[k], separators=(',', ':')) for k in ('bounds', 'threshold_evidence', 'observed_min_scope', 'period_evidence', 'interpretation_evidence')},
                    'difference_bounds_json': json.dumps(difference.get('difference_bounds_db'))}),
                **{k: place[k] for k in ('site', 'longitude', 'latitude')},
                **{k: o[k] for k in ('source', 'metric', 'value_db', 'raw_value', 'status', 'units', 'reference_period_status', 'coverage_id', 'source_sha256', 'http_record', 'row', 'column')},
                'place': label, 'reference_period_start': period.get('start'), 'reference_period_end': period.get('end'),
                'cell_geojson': json.dumps(o['cell'], separators=(',', ':')) if o['cell'] else '',
                'reporting_cutoff_db': o['reporting_cutoff_db'] if result['schema_version'] == 2 else result['reporting_cutoff_db'][o['metric']],
                'difference_from_first_db': difference.get('difference_from_first_db'),
                'difference_status': difference['status'], 'same_native_cell': difference.get('same_native_cell')})
    return output.getvalue().encode('utf-8-sig')
