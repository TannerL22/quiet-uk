import csv
import io
import json

import pytest

from quiet_uk.evidence_semantics import interpret, difference_bounds, record_evidence
from quiet_uk.source_pilot import SourcePilot, publish_interpretation, file_hash, json_bytes
from quiet_uk.catalogue import CatalogueIntegrityError
from quiet_uk.comparison import compare_places, comparison_csv
from test_source_pilot import pilot, coords
from test_comparison import place


@pytest.mark.parametrize('raw,masked,encoding', [(0, False, 'zero_code'), (-96, True, 'tiff_nodata'), (0, True, 'tiff_nodata')])
def test_missing_encodings_do_not_establish_bounds(raw, masked, encoding):
    o = interpret(raw, inside=True, masked=masked)
    assert o['raw_encoding'] == encoding
    assert o['status'] == 'unreported_unknown'
    assert o['value_db'] is o['bounds'] is None


def test_extract_boundary_and_known_domain_exclusion_are_different():
    assert interpret(None, inside=False)['status'] == 'outside_extract'
    assert interpret(0, inside=True, domain=False)['status'] == 'outside_domain'
    assert interpret(0, inside=True)['status'] == 'unreported_unknown'


def test_real_aircraft_minimum_is_not_a_cutoff():
    from pathlib import Path
    fixture = json.loads((Path(__file__).parent/'fixtures/provider/fixture.json').read_text('utf-8'))
    record = next(r for r in fixture['records'] if r['id'] == 'heathrow-aircraft-Lnight')
    evidence = record_evidence(record, fixture['products']['aircraft'], fixture['files'])
    assert evidence['provider_minimum_reporting_level_db'] == 35
    assert evidence['observed_min_reported_db'] == 44
    assert evidence['reporting_cutoff_db'] is None
    assert evidence['period_evidence']['assessed_period'] is None
    assert evidence['period_evidence']['provider_declared_period'] is None


def test_only_verified_encoding_inside_verified_domain_can_bound_zero():
    rule = {'status': 'verified', 'cutoff_db': 40, 'evidence_ref': 'synthetic-encoding-and-domain-test'}
    with pytest.raises(ValueError, match='domain'):
        interpret(0, inside=True, verified_zero_rule=rule)
    with pytest.raises(ValueError, match='evidence'):
        interpret(0, inside=True, domain=True, verified_zero_rule={**rule, 'evidence_ref': None})
    below = interpret(0, inside=True, domain=True, verified_zero_rule=rule)
    reported = interpret(52.3, inside=True)
    delta = difference_bounds(below, reported)
    assert delta['lower_db'] == pytest.approx(12.3)
    assert delta['lower_inclusive'] is False and delta['upper_db'] is None
    reverse = difference_bounds(reported, below)
    assert reverse['upper_db'] == pytest.approx(-12.3) and reverse['upper_inclusive'] is False
    assert difference_bounds(below, below) is None
    assert difference_bounds(interpret(0, inside=True), reported) is None
    # A verified zero rule never reinterprets TIFF nodata.
    assert interpret(-96, inside=True, masked=True, domain=True, verified_zero_rule=rule)['bounds'] is None


@pytest.fixture
def interpreted(pilot, tmp_path_factory):
    before = file_hash(pilot.root/'manifest.json')
    output = tmp_path_factory.mktemp('interpretation')/'release'
    publish_interpretation(pilot.root, output)
    assert file_hash(pilot.root/'manifest.json') == before
    return SourcePilot(output)


def test_versioned_release_preserves_parent_and_reproduces_display(interpreted, pilot):
    assert interpreted.manifest['parent_release_id'] == pilot.manifest['release_id']
    assert interpreted.manifest['release_id'] != pilot.manifest['release_id']
    assert 'reporting_cutoff_db' not in interpreted.manifest
    for name, digest in pilot.manifest['files'].items():
        assert file_hash(interpreted.root/name) == digest
    assert interpreted.verify(reproduce=True)['verified_records'] == 9
    assert pilot.verify(reproduce=True)['verified_records'] == 9
    with pytest.raises(FileExistsError):
        publish_interpretation(pilot.root, interpreted.root)
    with pytest.raises(ValueError, match='separate'):
        publish_interpretation(pilot.root, pilot.root/'child')


def test_new_point_and_csv_contract_agree(interpreted):
    result = compare_places(interpreted, [place(interpreted, 0), place(interpreted, 1, 'Nodata')], interpreted.manifest['release_id'])
    rows = list(csv.DictReader(io.StringIO(comparison_csv(result).decode('utf-8-sig'))))
    assert result['schema_version'] == 2 and 'reporting_cutoff_db' not in result
    for row in rows:
        assert row['status'] == 'unreported_unknown' and row['value_db'] == ''
        assert json.loads(row['bounds_json']) is None
        assert row['raw_encoding'] == ('zero_code' if row['place'] == 'A' else 'tiff_nodata')
        if row['source'] == 'aircraft':
            assert row['reporting_cutoff_db'] == ''
            assert row['reporting_cutoff_status'] == 'airport_specific_not_established'
            assert json.loads(row['period_evidence_json'])['provider_declared_period'] is None
        else:
            assert float(row['reporting_cutoff_db']) == (35 if row['metric'] == 'Lnight' else 40)
        assert json.loads(row['observed_min_scope_json'])['source_sha256'] == row['source_sha256']
    reported = interpreted.lookup('heathrow', *coords(interpreted, col=3))['observations']
    assert all(o['value_db'] == 65 and o['bounds']['lower_db'] == 65 for o in reported)
    assert all(o['status'] == 'outside_extract' for o in interpreted.lookup('heathrow', -2, 53)['observations'])


def test_interpretation_cannot_change_an_original_even_with_rehashed_manifest(interpreted):
    import hashlib
    manifest = interpreted.manifest
    manifest['records'][0]['evidence']['reporting_cutoff_db'] = 49
    manifest.pop('release_id')
    manifest['release_id'] = 'pilot-'+hashlib.sha256(json_bytes(manifest)).hexdigest()[:20]
    (interpreted.root/'manifest.json').write_bytes(json_bytes(manifest))
    with pytest.raises(CatalogueIntegrityError, match='original evidence'):
        SourcePilot(interpreted.root)


def test_qualified_bound_difference_and_csv_preserve_strictness(interpreted, monkeypatch):
    original = interpreted.lookup
    a, b = place(interpreted, 0), place(interpreted, 3, 'B')
    def lookup(site, lon, lat):
        result = original(site, lon, lat)
        if lon == a['longitude']:
            for o in result['observations']:
                o.update(interpret(0, inside=True, domain=True, verified_zero_rule={
                    'status': 'verified', 'cutoff_db': 40, 'evidence_ref': 'synthetic-test-only'}))
        return result
    monkeypatch.setattr(interpreted, 'lookup', lookup)
    result = compare_places(interpreted, [a,b], interpreted.manifest['release_id'])
    delta = result['rows'][0]['differences_from_first'][0]
    assert delta['difference_from_first_db'] is None
    assert delta['status'] == 'comparable_model_bounds'
    assert delta['difference_bounds_db'] == {'lower_db': 25, 'upper_db': None, 'lower_inclusive': False, 'upper_inclusive': False}
    aircraft = next(r for r in result['rows'] if r['source'] == 'aircraft')
    assert aircraft['differences_from_first'][0]['status'] == 'reference_period_unspecified'
    rows = list(csv.DictReader(io.StringIO(comparison_csv(result).decode('utf-8-sig'))))
    assert json.loads(rows[9]['difference_bounds_json']) == delta['difference_bounds_db']
