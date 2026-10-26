"""Independent population, native-history and source/air audit of budget seeds.

Does not import the budget controller or adaptive quadrature implementation.
The latest ground-fix receipt authorises the two historical physics revisions;
all current source files must match that receipt and the executed snapshots.
"""
import argparse
from collections import Counter
import gzip
import hashlib
import importlib.util
import json
import math
from pathlib import Path
import shutil
import sys

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.explorer import file_hash, json_bytes

spec = importlib.util.spec_from_file_location('budget_independent_source_review', ROOT/'scripts/90_verify_conditional_road_integration.py')
review = importlib.util.module_from_spec(spec)
spec.loader.exec_module(review)
MODEL_RECEIPT = ROOT/'artifacts/ground_average_validation_v2.json'


def read(path):
    return json.loads(Path(path).read_bytes())


def key(row):
    return row['link_id']+'|'+row['physical_case_id']


def point_key(node):
    return node['source_cell_id'], node['fraction']


def verified_inputs(inputs):
    """Fail closed unless sealed inputs use the currently validated model."""
    inputs = Path(inputs).resolve()
    review.helper.verified(inputs)
    protocol = read(inputs/'protocol.json')
    assert protocol['model_validation_sha256'] == file_hash(MODEL_RECEIPT)
    receipt = read(MODEL_RECEIPT)
    assert receipt['status'] == 'passed_ground_average_fix_validation'
    for relative, sha in receipt['code_sha256'].items():
        assert file_hash(ROOT/relative) == sha, 'Validated model changed: '+relative
    for relative, sha in read(inputs/'provenance.json')['code_sha256'].items():
        assert file_hash(ROOT/relative) == sha
        assert file_hash(inputs/'code'/Path(relative).name) == sha
    assert protocol['old_ledger_sha256'] == receipt['postcheck_ledger']['sha256']
    assert protocol['old_ledger_path'] == receipt['postcheck_ledger']['path']
    assert file_hash(ROOT/protocol['old_ledger_path']) == protocol['old_ledger_sha256']
    parents = {name: ROOT/'artifacts'/name for name in protocol['parent_inventory_sha256']}
    for name, parent in parents.items():
        review.helper.verified(parent)
        assert file_hash(parent/'inventory.json') == protocol['parent_inventory_sha256'][name]
    for path in (ROOT/'artifacts/ground_average_replay_v1/code').glob('*.py'):
        if path.name == '107_replay_ground_average_fix.py':
            continue
        assert file_hash(path) == file_hash(inputs/'code'/path.name)
    return protocol, parents


def scene_row(node, inputs, parents):
    reference = node['kernel_scene_reference']
    directory = inputs if reference['directory'] == inputs.name else parents[reference['directory']]
    path = (directory/reference['path']).resolve()
    assert path.is_relative_to(directory/'point_scenes')
    with gzip.open(path, 'rt', encoding='utf-8') as stream:
        for index, line in enumerate(stream):
            if index == reference['record_id']:
                scene = json.loads(line)
                assert scene['record_id'] == index
                assert scene['xy_bng'] == node['xy_bng']
                assert scene['native_profile_sha256'] == node['native_profile_sha256']
                return dict(node, candidate_kernel_scene=scene['candidate_kernel_scene'])
    raise AssertionError('Missing kernel scene record')


def run(inputs, output):
    inputs, output = Path(inputs).resolve(), Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Fresh workspace review required')
    protocol, parents = verified_inputs(inputs)
    assert protocol['kind'] == 'budget_road_observations_v1'
    input_hash = file_hash(inputs/'inventory.json')
    code_paths = [Path(__file__), *[ROOT/'scripts'/n for n in (
        '90_verify_conditional_road_integration.py', '86_verify_road_operating_inputs.py',
        '88_verify_physical_scene_assumptions.py', '81_build_real_road_scenes.py',
        '57_acquire_ground_cover_sources.py')], *(ROOT/'src/quiet_uk').glob('*.py')]
    codes = {path: file_hash(path) for path in code_paths}
    selected = [r for name in ('adaptive_road_integration_v3', 'corrected_road_integration_v1')
                for r in read(parents[name]/'refined_roads.json')['roads']
                if r['termination'] == 'initial_partition_budget_exhausted']
    selected.sort(key=lambda r: (r['minimum_initial_observations'], key(r)))
    assert len(selected) == len({key(r) for r in selected}) == 24
    plan = protocol['selected_identities']
    assert [key(r) for r in plan] == [key(r) for r in selected]
    source_map = {r['link_id']: r for r in read(parents['real_road_scenes_v2']/'source_lines.json')['source_roads']}
    old_seeds = read(parents['adaptive_road_integration_v3']/'seed_evaluations.json')
    corrected_seeds = read(parents['corrected_road_observations_v2']/'seed_evaluations.json')
    development = read(parents['development_road_observations_v1']/'receipt.json')['case_counts']
    historical = read(inputs/'historical_seeds.json')
    fresh = read(inputs/'seed_evaluations.json')
    expected = {key(r): corrected_seeds.get(key(r), old_seeds[key(r)]) for r in selected}
    assert historical == expected and set(fresh) == set(expected)
    ledger = {key(r): r for r in read(ROOT/protocol['old_ledger_path'])['roads']}
    counts = Counter()
    roads = {r['link_id']: r for r in read(parents['road_operating_inputs_v2']/'road_operating_inputs.json')['roads']}
    coefficients = read(parents['road_emission_assumptions_v2']/'published_2021_coefficients.json')
    calendar = read(parents['road_operating_inputs_v2']/'traffic_weather_calendars.json')['calendars']['national_vehicle']
    groups = read(inputs/'air_tables.json')['groups']
    for name in ('air_tables.json', 'air_tables.npz'):
        assert file_hash(inputs/name) == file_hash(parents['adaptive_road_integration_v3']/name)
    with np.load(inputs/'air_tables.npz', allow_pickle=False) as archive:
        air = {k: archive[k] for k in archive.files}
    samples = []
    with gzip.open(inputs/'point_observations.jsonl.gz', 'rt', encoding='utf-8') as all_nodes:
        for declared, prior in zip(plan, selected):
            identity = key(prior)
            assert ledger[identity]['whole_road_relative_energy'] is None
            assert ledger[identity]['integration_accuracy_outstanding']
            assert declared['minimum_initial_observations'] == prior['minimum_initial_observations']
            assert declared['historical_observations'] == len(expected[identity])
            assert declared['controls'] == dict(tolerance_db=.1, maximum_panel_m=4., minimum_panel_m=.01,
                maximum_refinements=512, maximum_evaluations=prior['minimum_initial_observations']+len(expected[identity])+5120)
            if identity in development:
                assert identity in corrected_seeds
                assert len(expected[identity]) == development[identity] == 2048
            else:
                assert prior['evaluation_count'] == len(old_seeds[identity])
            index = {point_key(n): n for n in expected[identity]}
            assert len(index) == len(expected[identity])
            for old in old_seeds[identity]:
                assert old['xy_bng'] == index[point_key(old)]['xy_bng']
                assert old['native_profile_sha256'] == index[point_key(old)]['native_profile_sha256']
            cells = {c['segment_id']: c for c in source_map[prior['link_id']]['source_cells']}
            power = review.group_source_power(roads[prior['link_id']], coefficients, calendar, groups)
            assert len(fresh[identity]) == len(expected[identity])
            path = inputs/'point_scenes'/(identity.replace('|', '__')+'.jsonl.gz')
            with gzip.open(path, 'rt', encoding='utf-8') as scenes:
                for ni, (old, node) in enumerate(zip(expected[identity], fresh[identity])):
                    assert json.loads(next(all_nodes)) == node
                    assert key(node) == identity and point_key(node) == point_key(old)
                    cell = cells[node['source_cell_id']]
                    t = node['fraction']
                    assert 0 < t < 1
                    xy = [a+(b-a)*t for a, b in zip(cell['start_xy_bng'], cell['end_xy_bng'])]
                    assert node['xy_bng'] == old['xy_bng'] == xy
                    assert node['native_profile_sha256'] == old['native_profile_sha256']
                    assert node['historical_observation_sha256'] == hashlib.sha256(json_bytes(old)).hexdigest()
                    assert node['historical_native_replay'] and node['historical_status'] == old['status']
                    reference = node['kernel_scene_reference']
                    assert reference == dict(directory=inputs.name, path=str(path.relative_to(inputs)).replace('\\', '/'), record_id=ni)
                    scene = json.loads(next(scenes))
                    assert scene['record_id'] == ni and scene['xy_bng'] == xy
                    assert scene['native_profile_sha256'] == node['native_profile_sha256']
                    if node['status'] == 'withheld':
                        assert node.get('relative_energy_per_m') is None and node['reasons']
                        counts['remaining_unknown_observations'] += 1
                    else:
                        assert node['status'] == 'conditional_research'
                        assert node['input_scene_kind'] == 'conditional_real_input'
                        expected_power = np.zeros((3, 8))
                        distance = node['direct_distance_m']
                        for pi, period in enumerate(review.PERIODS):
                            for bi, frequency in enumerate(review.BANDS):
                                expected_power[pi, bi] = math.fsum(power[g, pi, bi]*np.interp(
                                    distance, air[f'distance_{frequency}'], air[f'{period}_{frequency}'][g]) for g in range(len(groups)))
                        assert np.allclose(expected_power, node['joint_hourly_air_source_power_per_m'], rtol=3e-10, atol=1e-280)
                        transfer = np.asarray(node['relative_transfer'])
                        assert transfer.shape == (2, 8) and np.isfinite(transfer).all() and np.all(transfer >= 0)
                        assert np.allclose(transfer[:, None, :]*expected_power[None, :, :], node['relative_energy_per_m'], rtol=3e-10, atol=1e-280)
                        if old['status'] == 'conditional_research':
                            assert np.allclose(old['relative_transfer'], transfer, rtol=3e-11, atol=1e-280), 'Historical numeric transfer changed'
                            counts['historical_numeric_transfer_parity'] += 1
                        else:
                            counts['historical_unknowns_recovered'] += 1
                        counts['independent_source_air_band_values'] += 24
                        counts['point_energy_values_checked'] += 48
                        if not ni:
                            assert scene['candidate_kernel_scene'] is not None
                            samples.append(dict(node, candidate_kernel_scene=scene['candidate_kernel_scene']))
                    counts['native_history_records_checked'] += 1
                assert next(scenes, None) is None
            counts['complete_history_cases_checked'] += 1
        assert next(all_nodes, None) is None
    report = read(inputs/'report.json')
    assert report['native_replayed'] == counts['native_history_records_checked']
    assert report['unknown_observations'] == counts['remaining_unknown_observations']
    assert report['minimum_initial_observations'] == sum(r['minimum_initial_observations'] for r in selected) == 102400
    assert report['maximum_total_observations'] == sum(r['controls']['maximum_evaluations'] for r in plan)
    assert report['maximum_case_observations'] == max(r['controls']['maximum_evaluations'] for r in plan)
    print(f'History and source/air accounting passed; independently replaying {len(samples)} native profiles', flush=True)
    builder = review.builder_module.load_builder(parents['physical_scene_inputs_v2'], parents['ground_cover_inputs_v3'], maximum_reflections=0)
    features = read(parents['physical_scene_assumptions_v2']/'physical_features.json')['features']
    cases = {c['case_id']: c for c in protocol['physical_cases']}
    from quiet_uk import favourable_propagation as core
    from quiet_uk.conditional_scene_domain import execution_policy
    for node in samples:
        review.native_replay(node, builder, features, cases[node['physical_case_id']], protocol['receiver_xy_bng'], counts)
        scene = dict(node['candidate_kernel_scene'], conditional_input_policy=execution_policy(cases[node['physical_case_id']]))
        for bi, frequency in enumerate(review.BANDS):
            result = core.direct_attenuation(scene, frequency, dict(temperature_c=20., relative_humidity_percent=70., pressure_kpa=101.325, uniform_air=True), absorption_frequency_policy='exact_base10')
            assert result['status'] == 'conditional_research'
            for si, state in enumerate(('homogeneous', 'favourable')):
                value = 10**(-(result[state+'_attenuation_db']-result['atmospheric_attenuation_db'])/10)
                assert math.isclose(value, node['relative_transfer'][si][bi], rel_tol=3e-11, abs_tol=1e-280)
            counts['independent_uncached_kernel_bands_replayed'] += 1
    verified_inputs(inputs)
    assert input_hash == file_hash(inputs/'inventory.json')
    assert all(file_hash(path) == sha for path, sha in codes.items())
    output.mkdir(parents=True)
    (output/'code').mkdir()
    for path in codes:
        shutil.copyfile(path, output/'code'/path.name)
    (output/'report.json').write_bytes(json_bytes(dict(status='passed_independent_budget_observation_review',
        input_inventory_sha256=input_hash, counts=counts, input_summary=report,
        model_validation_sha256=file_hash(MODEL_RECEIPT),
        code_sha256={str(p.relative_to(ROOT)).replace('\\', '/'): sha for p, sha in codes.items()},
        field_validation=False, all_inclusive_noise_db=None, map_updated=False)))
    generation = review.helper.seal(output, 'budget-observation-review-')
    print(json.dumps(dict(generation_id=generation, counts=counts)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--inputs', required=True)
    parser.add_argument('--output', required=True)
    args = parser.parse_args()
    run(args.inputs, args.output)
