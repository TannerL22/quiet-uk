"""Comparisons must retain native evidence, unknowns and period boundaries."""
import csv
import io
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.parse import urlencode
from urllib.request import urlopen, Request

import pytest

from quiet_uk.comparison import compare_places, comparison_csv, validate_places
from quiet_uk.catalogue import CatalogueIntegrityError
from quiet_uk.explorer_server import ExplorerServer
from test_source_pilot import pilot, coords


def place(pilot, col=2, label='A'):
    lon, lat = coords(pilot, col=col)
    return {'label': label, 'site': 'heathrow', 'longitude': lon, 'latitude': lat}


def test_exact_values_and_declared_period_differences(pilot):
    result = compare_places(pilot, [place(pilot), place(pilot, 3, 'B')], pilot.manifest['release_id'])
    for row in result['rows']:
        assert [o['value_db'] for o in row['observations']] == ([35, 65] if row['metric'] == 'Lnight' else [40, 65])
        delta = row['differences_from_first'][0]
        if row['source'] == 'aircraft':
            assert delta['difference_from_first_db'] is None
            assert delta['status'] == 'reference_period_unspecified'
        else:
            assert delta['difference_from_first_db'] == (30 if row['metric'] == 'Lnight' else 25)
            assert delta['same_native_cell'] is False
    assert result['products']['road']['metadata_id'] == pilot.manifest['products']['road']['metadata_id']
    assert result['places'][1]['observations'] == pilot.lookup('heathrow', *coords(pilot, col=3))['observations']


@pytest.mark.parametrize('col,raw', [(0, 0), (1, -96)])
def test_unknown_values_are_not_zero_or_ranked(pilot, col, raw):
    result = compare_places(pilot, [place(pilot), place(pilot, col, 'Unknown')], pilot.manifest['release_id'])
    for row in result['rows']:
        assert row['observations'][1]['value_db'] is None
        assert row['observations'][1]['raw_value'] == raw
        assert row['differences_from_first'][0]['difference_from_first_db'] is None
        assert row['differences_from_first'][0]['status'] == 'unreported_or_outside'


def test_outside_and_same_cell_are_explicit(pilot):
    a = place(pilot)
    b = {**a, 'label': 'Same cell', 'longitude': a['longitude'] + .000001}
    c = {**a, 'label': 'Outside', 'longitude': -2, 'latitude': 53}
    result = compare_places(pilot, [a, b, c], pilot.manifest['release_id'])
    row = result['rows'][0]
    assert row['differences_from_first'][0]['same_native_cell'] is True
    assert row['differences_from_first'][0]['difference_from_first_db'] == 0
    assert row['observations'][2]['status'] == 'outside_pilot'
    assert row['observations'][2]['raw_value'] is None


def test_stale_release_and_tampered_original_cannot_export(pilot):
    points = [place(pilot), place(pilot, 3, 'B')]
    with pytest.raises(ValueError, match='release changed'):
        compare_places(pilot, points, 'pilot-old')
    record = pilot.manifest['records'][0]
    with (pilot.root/record['path']).open('ab') as stream:
        stream.write(b'tampered')
    with pytest.raises(CatalogueIntegrityError):
        compare_places(pilot, points, pilot.manifest['release_id'])


def test_differing_periods_cannot_produce_difference(pilot, monkeypatch):
    original = pilot.lookup
    a, b = place(pilot), place(pilot, 3, 'B')
    def lookup(site, lon, lat):
        result = original(site, lon, lat)
        if lon == b['longitude']:
            for o in result['observations']:
                o['reference_period'] = {'start': '2022-01-01', 'end': '2022-12-31'}
        return result
    monkeypatch.setattr(pilot, 'lookup', lookup)
    result = compare_places(pilot, [a,b], pilot.manifest['release_id'])
    assert result['rows'][0]['differences_from_first'][0]['status'] == 'incompatible_evidence'


def test_csv_is_exact_qualified_and_formula_safe(pilot):
    result = compare_places(pilot, [place(pilot, 0, '=SUM(1,2)'), place(pilot, 3, 'Name, with "quotes"')], pilot.manifest['release_id'])
    rows = list(csv.DictReader(io.StringIO(comparison_csv(result).decode('utf-8-sig'))))
    assert len(rows) == 18
    assert rows[0]['place'] == "'=SUM(1,2)"
    assert rows[0]['value_db'] == '' and rows[0]['raw_value'] == '0.0'
    assert rows[0]['status'] == 'unreported' and rows[0]['difference_status'] == 'baseline'
    assert rows[9]['place'] == 'Name, with "quotes"'
    assert float(rows[9]['value_db']) == result['places'][1]['observations'][0]['value_db']
    assert len(rows[9]['source_sha256']) == 64
    assert json.loads(rows[9]['cell_geojson']) == result['places'][1]['observations'][0]['cell']
    assert all(r['missing_value_policy'] and r['uncertainty'] and r['licence_url'] for r in rows)


@pytest.mark.parametrize('points', [None, {}, [], [None], [{'site': 'heathrow'}]])
def test_malformed_lists_rejected(points):
    with pytest.raises(ValueError): validate_places(points, {'heathrow': {}})


@pytest.mark.parametrize('field,value', [('longitude', True), ('latitude', float('nan')), ('longitude', 181), ('latitude', '51'), ('label', ''), ('label', 'x'*61), ('label', 'a\nb'), ('site', []), ('site', 'missing')])
def test_invalid_place_rejected(field, value):
    p = {'site': 'heathrow', 'longitude': -.45, 'latitude': 51.46, 'label': 'A', field: value}
    with pytest.raises(ValueError): validate_places([p], {'heathrow': {}})


def test_duplicates_and_budget_checked_before_lookup(pilot, monkeypatch):
    p = place(pilot)
    monkeypatch.setattr(pilot, 'lookup', lambda *a: pytest.fail('Invalid input must fail before raster IO'))
    for places in ([p,p], [p]*4):
        with pytest.raises(ValueError): compare_places(pilot, places, pilot.manifest['release_id'])


def test_http_downloads_are_bounded_release_pinned_and_local(pilot):
    server = ExplorerServer(0, None, Path(__file__).parents[1]/'explorer', pilot=pilot)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    base = f'http://127.0.0.1:{server.server_port}'
    params = {'places': json.dumps([place(pilot), place(pilot, 3, 'B')]), 'release': pilot.manifest['release_id']}
    try:
        assert urlopen(base+'/comparison.js').status == 200
        data = json.load(urlopen(base+'/api/pilot/comparison?'+urlencode(params)))
        for suffix in ('json', 'csv'):
            with urlopen(base+'/downloads/pilot-comparison.'+suffix+'?'+urlencode(params)) as response:
                assert 'attachment;' in response.headers['Content-Disposition']
                body = response.read()
                if suffix == 'json': assert json.loads(body) == data
                else: assert len(list(csv.DictReader(io.StringIO(body.decode('utf-8-sig'))))) == 18
        for bad in ({**params, 'release':'old'}, {**params, 'places':'null'}, {**params, 'places':'x'*2501}, {**params, 'places':'['}, {**params, 'release':['one','two']}):
            with pytest.raises(HTTPError) as exc: urlopen(base+'/api/pilot/comparison?'+urlencode(bad, doseq=True))
            assert exc.value.code == 400
        with pytest.raises(HTTPError) as exc:
            urlopen(Request(base+'/api/pilot/comparison?'+urlencode(params), headers={'Origin':'https://example.org'}))
        assert exc.value.code == 403
    finally:
        server.shutdown(); server.server_close(); thread.join(5)
