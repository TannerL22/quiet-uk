"""Independent accounting, exact ground oracle and receipt for the rounding fix."""
import argparse
import ast
from collections import Counter
from fractions import Fraction
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys
import xml.etree.ElementTree as ET
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.explorer import file_hash, json_bytes
from quiet_uk.terrain_profile import linear_segments
from quiet_uk.sightline_boundary import composite_surface, validated_roofs
from quiet_uk.terrain_boundary import side_ground


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


scalar = module('ground_fix_independent_accounting', ROOT/'scripts/90_verify_conditional_road_integration.py')
helper = scalar.helper


def read(path):
    return json.loads(Path(path).read_bytes())


def key(row):
    return row['link_id'], row['physical_case_id']


def array_close(a, b):
    assert np.allclose(a, b, rtol=3e-10, atol=1e-280)


def old_nodes(directory, selected):
    found = []
    with gzip.open(directory/'node_evaluations.jsonl.gz', 'rt') as stream:
        for line in stream:
            node = json.loads(line)
            if key(node) == selected:
                found.append(node)
            elif found:
                break
    return found


def run(inputs, output, junit, receipt):
    inputs, output, junit, receipt = map(lambda p: Path(p).resolve(), (inputs, output, junit, receipt))
    if output.exists() or receipt.exists() or not all(p.is_relative_to(ROOT/'artifacts') for p in (output, receipt)):
        raise ValueError('Fresh workspace review and receipt required')
    review_paths = [Path(__file__), ROOT/'scripts/107_replay_ground_average_fix.py',
        ROOT/'tests/test_ground_average_rounding.py', *[ROOT/'scripts'/name for name in (
            '90_verify_conditional_road_integration.py', '88_verify_physical_scene_assumptions.py',
            '86_verify_road_operating_inputs.py', '81_build_real_road_scenes.py',
            '57_acquire_ground_cover_sources.py')], *(ROOT/'src/quiet_uk').glob('*.py')]
    executed_hashes = {path: file_hash(path) for path in review_paths}
    junit_hash = file_hash(junit)
    helper.verified(inputs)
    input_sha = file_hash(inputs/'inventory.json')
    protocol = read(inputs/'protocol.json')
    assert protocol['kind'] == 'ground_average_fix_native_replay_v1'
    selected = protocol['link_id'], protocol['physical_case_id']
    parents = {n: ROOT/'artifacts'/n for n in protocol['parent_inventory_sha256']}
    for name, path in parents.items():
        helper.verified(path)
        assert file_hash(path/'inventory.json') == protocol['parent_inventory_sha256'][name]
    primary = parents['corrected_road_integration_v1']
    fine = parents['corrected_road_spacing_check_v1']
    prior_review = parents['corrected_road_integration_review_v1']
    for path in review_paths:
        if (prior_review/path.name).exists():
            assert file_hash(path) == file_hash(prior_review/path.name)
    old_code = fine/'code'
    for path in (inputs/'code').glob('*.py'):
        live = ROOT/('scripts' if path.name[:2].isdigit() else 'src/quiet_uk')/path.name
        assert file_hash(path) == file_hash(live)
        if path.name != 'terrain_boundary.py' and (old_code/path.name).exists():
            assert file_hash(path) == file_hash(old_code/path.name)
    old_tree = ast.parse((old_code/'terrain_boundary.py').read_text(encoding='utf-8'))
    new_tree = ast.parse((inputs/'code/terrain_boundary.py').read_text(encoding='utf-8'))
    old_functions = {n.name: ast.dump(n) for n in old_tree.body if isinstance(n, ast.FunctionDef)}
    new_functions = {n.name: ast.dump(n) for n in new_tree.body if isinstance(n, ast.FunctionDef)}
    assert {n for n in old_functions if old_functions[n] != new_functions[n]} == {'ground_average', 'side_ground'}
    assert set(new_functions)-set(old_functions) == {'_ground_parts', '_derived_ground_average'}
    def other_statements(tree):
        return [ast.dump(n) for n in tree.body if not isinstance(n, ast.FunctionDef)
                and not (isinstance(n, ast.ImportFrom) and n.module == 'fractions')]
    assert other_statements(old_tree) == other_statements(new_tree)
    assert [ast.dump(n) for n in new_tree.body if isinstance(n, ast.ImportFrom) and n.module == 'fractions'] == [
        ast.dump(ast.parse('from fractions import Fraction').body[0])]
    before = {stage: old_nodes(path, selected) for stage, path in (('primary', primary), ('finer', fine))}
    after = {}
    counts = Counter()
    for stage in ('primary', 'finer'):
        with gzip.open(inputs/(stage+'_nodes.jsonl.gz'), 'rt') as stream:
            after[stage] = [json.loads(line) for line in stream]
        assert len(before[stage]) == len(after[stage])
        for index, (old, new) in enumerate(zip(before[stage], after[stage])):
            assert key(new) == selected and new['node_id'] == index
            assert new['prior_record_sha256'] == hashlib.sha256(json_bytes(old)).hexdigest()
            assert new['native_profile_sha256'] == old['native_profile_sha256']
            for field in ('xy_bng', 'source_cell_id', 'fraction'):
                assert new[field] == old[field]
            assert new['status'] == 'conditional_research'
            if old['status'] == 'conditional_research':
                for field in ('relative_transfer', 'joint_hourly_air_source_power_per_m', 'relative_energy_per_m'):
                    assert np.array_equal(new[field], old[field])
                counts['prior_numeric_observations_bit_identical'] += 1
            else:
                assert old['reasons'] == ['unsupported_cross_section:Ground factor must lie in [0, 1]']
                counts['recovered_rounding_observations'] += 1
            counts['native_input_identities_checked'] += 1
    assert counts['prior_numeric_observations_bit_identical'] == 628 and counts['recovered_rounding_observations'] == 1
    recovered = next(new for old, new in zip(before['finer'], after['finer']) if old['status'] == 'withheld')
    diagnostic = read(ROOT/'artifacts/ground_average_rounding_diagnostic_v1.json')
    assert recovered['native_profile_sha256'] == diagnostic['point']['native_profile_sha256']
    scene = recovered['candidate_kernel_scene']
    _, ground = composite_surface(linear_segments(scene['points']), scene['ground_segments'],
                                  validated_roofs(linear_segments(scene['points']), scene['obstacles']))
    start, end = diagnostic['derived_averages'][0]['start'], diagnostic['derived_averages'][0]['end']
    assert all(b == c for (a, b, g), (c, d, h) in zip(ground, ground[1:]))
    assert all(0 <= g <= 1 for a, b, g in ground)
    left, right = Fraction(start), Fraction(end)
    weights = [max(Fraction(0), min(Fraction(b), right)-max(Fraction(a), left)) for a, b, g in ground]
    assert sum(weights, Fraction(0)) == right-left
    exact = sum((weight*Fraction(g) for weight, (a, b, g) in zip(weights, ground)), Fraction(0))/(right-left)
    prior_boundary = module('ground_fix_frozen_old_means', old_code/'terrain_boundary.py')
    assert prior_boundary.side_ground(ground, start, end) == math.nextafter(1., math.inf)
    assert exact == 1 and side_ground(ground, start, end) == 1.
    source = next(r for r in read(parents['real_road_scenes_v2']/'source_lines.json')['source_roads'] if r['link_id'] == selected[0])
    cells = {c['segment_id']: c for c in source['source_cells']}
    road = next(r for r in read(parents['road_operating_inputs_v2']/'road_operating_inputs.json')['roads'] if r['link_id'] == selected[0])
    calendar = read(parents['road_operating_inputs_v2']/'traffic_weather_calendars.json')['calendars']['national_vehicle']
    coeff = read(parents['road_emission_assumptions_v2']/'published_2021_coefficients.json')
    groups = read(inputs/'air_tables.json')['groups']
    for name in ('air_tables.json', 'air_tables.npz'):
        assert file_hash(inputs/name) == file_hash(primary/name)
    with np.load(inputs/'air_tables.npz', allow_pickle=False) as archive:
        air = {name: archive[name] for name in archive.files}
    powers = scalar.group_source_power(road, coeff, calendar, groups)
    for nodes in after.values():
        for node in nodes:
            cell = cells[node['source_cell_id']]
            t = node['fraction']
            assert 0 < t < 1
            assert node['xy_bng'] == [a+(b-a)*t for a, b in zip(cell['start_xy_bng'], cell['end_xy_bng'])]
            expected = np.zeros((3, 8))
            for pi, p in enumerate(scalar.PERIODS):
                for bi, f in enumerate(scalar.BANDS):
                    expected[pi, bi] = math.fsum(powers[g, pi, bi]*np.interp(node['direct_distance_m'], air[f'distance_{f}'], air[f'{p}_{f}'][g]) for g in range(len(groups)))
            array_close(expected, node['joint_hourly_air_source_power_per_m'])
            array_close(np.asarray(node['relative_transfer'])[:, None, :]*expected[None, :, :], node['relative_energy_per_m'])
            counts['independent_source_air_band_values'] += 24
            counts['point_energy_values_checked'] += 48
    parent_row = read(inputs/'primary_road.json')
    assert parent_row == next(r for r in read(primary/'refined_roads.json')['roads'] if key(r) == selected)
    primary_total = np.zeros((2, 3, 8))
    for leaf in parent_row['leaves']:
        for node_id, t in zip(leaf['node_ids'][1:3], ((3*leaf['left_fraction']+leaf['right_fraction'])/4,
                                                   (leaf['left_fraction']+3*leaf['right_fraction'])/4)):
            node = after['primary'][node_id]
            assert node['source_cell_id'] == leaf['source_cell_id']
            scalar.close(node['fraction'], t)
            primary_total += np.asarray(node['relative_energy_per_m'])*leaf['length_m']/2
    array_close(primary_total, parent_row['whole_road_relative_energy'])
    total = np.zeros((2, 3, 8))
    length = 0.
    index = 0
    for li, leaf in enumerate(parent_row['leaves']):
        scalar.close(leaf['length_m'], cells[leaf['source_cell_id']]['length_m']*(leaf['right_fraction']-leaf['left_fraction']))
        for j in range(4):
            node = after['finer'][index]
            assert node['leaf_index'] == li and node['quadrature_j'] == j and node['quadrature_n'] == 4
            assert node['source_cell_id'] == leaf['source_cell_id']
            scalar.close(node['fraction'], leaf['left_fraction']+(leaf['right_fraction']-leaf['left_fraction'])*(j+.5)/4)
            weight = leaf['length_m']/4
            scalar.close(node['weight_length_m'], weight)
            assert weight > 0
            total += np.asarray(node['relative_energy_per_m'])*weight
            length += weight
            index += 1
    assert index == len(after['finer']) == 256
    scalar.close(length, source['prepared_length_m'])
    check = read(inputs/'finer_check.json')
    assert check['evaluation_count'] == 256 and not check['unknown_reason_counts']
    array_close(check['whole_road_relative_energy'], total)
    maximum = 0.
    for si, state in enumerate(('homogeneous', 'favourable')):
        previous = scalar.metrics(parent_row['whole_road_relative_energy'][si])
        for metric, value in scalar.metrics(total[si]).items():
            difference = value-previous[metric]
            scalar.close(difference, check['change_from_adaptive_db'][state][metric])
            maximum = max(maximum, abs(difference))
    scalar.close(maximum, check['maximum_absolute_change_db'])
    passed = maximum <= .1
    assert check['status'] == ('finer_spacing_check_passed' if passed else 'finer_spacing_check_withheld_or_changed')
    old_ledger_path = ROOT/'artifacts/corrected_road_postcheck_ledger_v1.json'
    assert file_hash(old_ledger_path) == protocol['prior_ledger_sha256']
    old_rows = read(old_ledger_path)['roads']
    ledger = read(inputs/'road_ledger.json')
    assert len(ledger['roads']) == len(old_rows) == 1444
    for old, new in zip(old_rows, ledger['roads']):
        assert key(old) == key(new)
        if key(old) != selected:
            assert old == new
            counts['unchanged_ledger_rows'] += 1
            continue
        expected = {k: v for k, v in old.items() if not k.startswith('finer_spacing_')}
        expected.update(status=parent_row['status'] if passed else 'withheld_after_finer_spacing_check',
            integration_stage='adaptive_refined' if passed else 'adaptive_refined_finer_check_withheld',
            integration_accuracy_outstanding=not passed,
            whole_road_relative_energy=parent_row['whole_road_relative_energy'] if passed else None,
            ground_average_revision='exact-derived-boundary-fallback-v1',
            finer_spacing_check_status=check['status'], finer_spacing_maximum_absolute_change_db=check['maximum_absolute_change_db'],
            finer_spacing_unknown_reason_counts={})
        assert new == expected
    affected = {key(r) for r in read(primary/'refined_roads.json')['roads']}
    retained = sum(key(r) in affected and not r['integration_accuracy_outstanding'] and r['whole_road_relative_energy'] is not None for r in ledger['roads'])
    summary = dict(affected_cases=59, affected_cases_passing_both_checks=retained,
        affected_cases_still_withheld=59-retained, finer_passed_including_controls=sum(
            r['finer_spacing_check_status'] == 'finer_spacing_check_passed' for r in ledger['roads']),
        other_ledger_rows_exactly_preserved=1443, road_ledger_cases=1444)
    assert ledger['summary'] == summary
    builder = scalar.builder_module.load_builder(parents['physical_scene_inputs_v2'], parents['ground_cover_inputs_v3'], maximum_reflections=0)
    features = read(parents['physical_scene_assumptions_v2']/'physical_features.json')['features']
    case = next(c for c in protocol['physical_cases'] if c['case_id'] == selected[1])
    for node in (after['primary'][0], recovered, after['finer'][-1]):
        scalar.native_replay(node, builder, features, case, protocol['receiver_xy_bng'], counts)
    xml = ET.parse(junit).getroot()
    tests = {name: sum(int(s.attrib.get(name, 0)) for s in xml.iter('testsuite')) for name in ('tests', 'errors', 'failures', 'skipped')}
    assert tests == dict(tests=1050, errors=0, failures=0, skipped=0)
    test_ids = {(c.attrib['classname'], c.attrib['name']) for c in xml.iter('testcase')}
    old_test_ids = {(c.attrib['classname'], c.attrib['name']) for c in ET.parse(ROOT/'artifacts/corrected_road_validation_v2.xml').getroot().iter('testcase')}
    assert len(test_ids) == 1050 and len(old_test_ids) == 1013 and old_test_ids < test_ids
    assert all(name == 'tests.test_ground_average_rounding' for name, test in test_ids-old_test_ids)
    assert not ledger['field_validation'] and not ledger['map_updated'] and ledger['all_inclusive_noise_db'] is None
    report = read(inputs/'report.json')
    assert report['summary'] == summary and report['counts']['prior_numeric_observations_bit_identical'] == 628
    assert all(file_hash(path) == value for path, value in executed_hashes.items())
    assert file_hash(junit) == junit_hash
    output.mkdir(parents=True)
    shutil.copyfile(__file__, output/Path(__file__).name)
    shutil.copyfile(ROOT/'tests/test_ground_average_rounding.py', output/'test_ground_average_rounding.py')
    code = output/'review_code'
    code.mkdir()
    for path in review_paths:
        shutil.copyfile(path, code/path.name)
    result = dict(status='passed_independent_ground_average_fix_review', input_inventory_sha256=input_sha,
        counts=counts, tests=tests, new_tests=37, summary=summary, finer_maximum_change_db=maximum,
        exact_ground_average=str(exact), old_ground_average=math.nextafter(1., math.inf), corrected_ground_average=1.,
        scope='All 629 native identities, 628 exact numeric comparisons, independent source/air and positive sums, exact interval mean, three native reconstructions and every ledger entry.',
        field_validation=False, all_inclusive_noise_db=None, map_updated=False)
    (output/'report.json').write_bytes(json_bytes(result))
    helper.verified(inputs)
    assert file_hash(inputs/'inventory.json') == input_sha
    generation = helper.seal(output, 'ground-average-fix-review-')
    final = dict(status='passed_ground_average_fix_validation', generations={inputs.name: read(inputs/'inventory.json')['generation_id'], output.name: generation},
        generation_inventory_sha256={inputs.name: input_sha, output.name: file_hash(output/'inventory.json')}, tests=tests, new_tests=37,
        junit_sha256=junit_hash, prior_validation_sha256=protocol['prior_validation_sha256'], summary=summary,
        replay=report, independent_review=result, postcheck_ledger=dict(path=(inputs/'road_ledger.json').relative_to(ROOT).as_posix(), sha256=file_hash(inputs/'road_ledger.json')),
        code_sha256={p.relative_to(ROOT).as_posix(): value for p, value in executed_hashes.items()},
        field_validation=False, complete_physical_path_support=False, rigorous_noise_error_bound_db=None, all_inclusive_noise_db=None, map_updated=False)
    receipt.write_bytes(json_bytes(final))
    print(json.dumps(dict(generation_id=generation, tests=tests, summary=summary, finer_maximum_change_db=maximum)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ('inputs', 'output', 'junit', 'receipt'):
        parser.add_argument('--'+name, required=True)
    args = parser.parse_args()
    run(args.inputs, args.output, args.junit, args.receipt)
