"""Fresh native replay of the road held by derived-ground rounding.

Preserves immutable earlier studies. Uses their exact observation positions,
the existing geographic evaluator and positive finer rule, with the reviewed
ground-average change as the sole source-code revision.
"""
import argparse
import ast
from collections import Counter
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import time
import numpy as np

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT/'src'))
from quiet_uk.explorer import file_hash, json_bytes


def module(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    result = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(result)
    return result


runner = module('ground_fix_native_runner', ROOT/'scripts/92_refine_conditional_roads.py')
finer = module('ground_fix_positive_rule', ROOT/'scripts/95_check_refined_road_spacing.py')
ledger_rule = module('ground_fix_postcheck_rule', ROOT/'scripts/94_record_adaptive_validation.py')
helper = runner.prior.helper
LINK = '04C8025A-1D65-448D-A443-07DAF07B9744'
CASE = 'combined_reference'
PRIMARY = 'corrected_road_integration_v1'
FINE = 'corrected_road_spacing_check_v1'
VALIDATION = ROOT/'artifacts/corrected_road_validation_v1.json'
OLD_LEDGER = ROOT/'artifacts/corrected_road_postcheck_ledger_v1.json'


def read(path):
    return json.loads(Path(path).read_bytes())


def identity(row):
    return row['link_id'], row['physical_case_id']


def observations(directory):
    found = []
    with gzip.open(directory/'node_evaluations.jsonl.gz', 'rt') as stream:
        for line in stream:
            node = json.loads(line)
            if identity(node) == (LINK, CASE):
                found.append(node)
            elif found:
                break
    assert found and [n['node_id'] for n in found] == list(range(len(found)))
    return found


def code_compatibility():
    """All earlier implementation bytes except the two ground means persist."""
    old_code = ROOT/'artifacts'/FINE/'code'
    for path in old_code.glob('*.py'):
        if path.name == 'terrain_boundary.py':
            continue
        live = ROOT/('scripts' if path.name[:2].isdigit() else 'src/quiet_uk')/path.name
        assert file_hash(path) == file_hash(live), 'Unrelated implementation changed: '+path.name
    old = ast.parse((old_code/'terrain_boundary.py').read_text(encoding='utf-8'))
    new = ast.parse((ROOT/'src/quiet_uk/terrain_boundary.py').read_text(encoding='utf-8'))
    current = {n.name: n for n in new.body if isinstance(n, ast.FunctionDef)}
    unchanged = []
    for node in old.body:
        if isinstance(node, ast.FunctionDef) and node.name not in ('ground_average', 'side_ground'):
            assert ast.dump(node) == ast.dump(current[node.name])
            unchanged.append(node.name)
    return unchanged


def run(output):
    start = time.perf_counter()
    output = Path(output).resolve()
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Fresh workspace generation required')
    old_receipt = read(VALIDATION)
    assert old_receipt['status'] == 'passed_corrected_road_integration_validation'
    assert file_hash(OLD_LEDGER) == old_receipt['postcheck_ledger']['sha256']
    names = list(dict.fromkeys([*runner.prior.PARENTS, *old_receipt['generation_inventory_sha256']]))
    parents = {name: ROOT/'artifacts'/name for name in names}
    for name, path in parents.items():
        helper.verified(path)
        if name in old_receipt['generation_inventory_sha256']:
            assert file_hash(path/'inventory.json') == old_receipt['generation_inventory_sha256'][name]
    primary, fine = parents[PRIMARY], parents[FINE]
    physical_hashes = read(primary/'protocol.json')['parent_inventory_sha256']
    assert all(file_hash(parents[n]/'inventory.json') == physical_hashes[n] for n in runner.prior.PARENTS)
    unchanged = code_compatibility()
    code_paths = [Path(__file__), *[ROOT/'scripts'/name for name in (
        '92_refine_conditional_roads.py', '95_check_refined_road_spacing.py',
        '94_record_adaptive_validation.py', '89_integrate_conditional_roads.py',
        '81_build_real_road_scenes.py', '57_acquire_ground_cover_sources.py')],
        *(ROOT/'src/quiet_uk').glob('*.py')]
    hashes = {path: file_hash(path) for path in code_paths}
    parent_hashes = {name: file_hash(path/'inventory.json') for name, path in parents.items()}
    primary_row = next(r for r in read(primary/'refined_roads.json')['roads'] if identity(r) == (LINK, CASE))
    old_nodes = {'primary': observations(primary), 'finer': observations(fine)}
    assert len(old_nodes['primary']) == 373 and len(old_nodes['finer']) == 256
    assert Counter(n['status'] for n in old_nodes['primary']) == {'conditional_research': 373}
    assert Counter(n['status'] for n in old_nodes['finer']) == {'conditional_research': 255, 'withheld': 1}
    output.mkdir(parents=True)
    for name in ('air_tables.json', 'air_tables.npz'):
        shutil.copyfile(primary/name, output/name)
    protocol = dict(kind='ground_average_fix_native_replay_v1', parent_inventory_sha256=parent_hashes,
        link_id=LINK, physical_case_id=CASE, controls=primary_row['controls'],
        prior_validation_sha256=file_hash(VALIDATION), prior_ledger_sha256=file_hash(OLD_LEDGER),
        old_source_directory=FINE, unchanged_boundary_functions=unchanged,
        selection='All 373 primary and 256 finer observations on the recorded rounding-held road.',
        method='Every point is evaluated afresh from native geography. Existing finite point transfers and source/air powers must remain bit-identical.',
        receiver_xy_bng=runner.prior.RECEIVER, physical_cases=runner.prior.CASES,
        field_validation=False, all_inclusive_noise_db=None, map_updated=False)
    (output/'protocol.json').write_bytes(json_bytes(protocol))
    (output/'seed_evaluations.json').write_bytes(json_bytes({LINK+'|'+CASE: []}))
    runner.initialise(output)
    runner.geometry_breakpoints = lambda *args, **kwargs: primary_row['partition']
    fresh = {'primary': [], 'finer': []}
    counts = Counter()

    def replay(source, evaluate, **kwargs):
        for stage, nodes in old_nodes.items():
            with (output/(stage+'_nodes.jsonl.gz')).open('wb') as raw:
                with gzip.GzipFile(filename='', mode='wb', fileobj=raw, mtime=0) as stream:
                    for old in nodes:
                        node = dict(evaluate(old['xy_bng']))
                        assert node['native_profile_sha256'] == old['native_profile_sha256']
                        if 'candidate_kernel_scene' in old:
                            assert node['candidate_kernel_scene'] == old['candidate_kernel_scene']
                        assert node['status'] == 'conditional_research', node.get('reasons')
                        if old['status'] == 'conditional_research':
                            for field in ('relative_transfer', 'joint_hourly_air_source_power_per_m', 'relative_energy_per_m'):
                                assert np.array_equal(node[field], old[field]), 'Numeric regression: '+field
                            counts['prior_numeric_observations_bit_identical'] += 1
                        else:
                            assert old['reasons'] == ['unsupported_cross_section:Ground factor must lie in [0, 1]']
                            counts['recovered_rounding_observations'] += 1
                        for field in ('node_id', 'source_cell_id', 'fraction', 'xy_bng', 'leaf_index',
                                      'quadrature_j', 'quadrature_n', 'weight_length_m'):
                            if field in old:
                                node[field] = old[field]
                        node.update(link_id=LINK, physical_case_id=CASE, replay_stage=stage,
                                    prior_record_sha256=hashlib.sha256(json_bytes(old)).hexdigest(),
                                    prior_status=old['status'], observation_origin='new_native_geographic_and_kernel_replay')
                        fresh[stage].append(node)
                        stream.write((json.dumps(node, allow_nan=False, separators=(',', ':'))+'\n').encode())
                        counts['native_observations_replayed'] += 1
                        if counts['native_observations_replayed'] % 64 == 0:
                            stream.flush()
                            print(f'{counts["native_observations_replayed"]}/629 native observations replayed', flush=True)
        return dict(evaluation_count=counts['native_observations_replayed'])

    runner.integrate_adaptive = replay
    source = next(s for s in read(parents['real_road_scenes_v2']/'source_lines.json')['source_roads'] if s['link_id'] == LINK)
    runner.compute((source, CASE))
    total = np.zeros((2, 3, 8))
    for leaf in primary_row['leaves']:
        for index in leaf['node_ids'][1:3]:
            total += np.asarray(fresh['primary'][index]['relative_energy_per_m'])*leaf['length_m']/2
    assert np.allclose(total, primary_row['whole_road_relative_energy'], rtol=3e-14, atol=0.)
    finer.BANK[(LINK, CASE)] = primary_row
    points = iter(fresh['finer'])

    def observe(xy):
        node = next(points)
        assert node['xy_bng'] == xy
        return node

    check = finer.finer_rule(source, observe, partition=primary_row['partition'], controls={},
        seed_evaluations=[dict(physical_case_id=CASE)])
    assert next(points, None) is None
    check.pop('evaluations')
    check.update(link_id=LINK, physical_case_id=CASE)
    (output/'primary_road.json').write_bytes(json_bytes(primary_row))
    (output/'finer_check.json').write_bytes(json_bytes(check))
    old_ledger = read(OLD_LEDGER)
    old_target = next(r for r in old_ledger['roads'] if identity(r) == (LINK, CASE))
    candidate = {k: v for k, v in old_target.items() if not k.startswith('finer_spacing_')}
    candidate.update(status=primary_row['status'], integration_stage='adaptive_refined',
        integration_accuracy_outstanding=False, whole_road_relative_energy=primary_row['whole_road_relative_energy'],
        ground_average_revision='exact-derived-boundary-fallback-v1')
    replacement = ledger_rule.postcheck_ledger([candidate], [check])[0]
    ledger = [replacement if identity(r) == (LINK, CASE) else r for r in old_ledger['roads']]
    affected = {identity(r) for r in read(primary/'refined_roads.json')['roads']}
    passing = sum(identity(r) in affected and not r['integration_accuracy_outstanding']
                  and r['whole_road_relative_energy'] is not None for r in ledger)
    summary = dict(affected_cases=59, affected_cases_passing_both_checks=passing,
        affected_cases_still_withheld=59-passing, finer_passed_including_controls=sum(
            r['finer_spacing_check_status'] == 'finer_spacing_check_passed' for r in ledger),
        other_ledger_rows_exactly_preserved=1443, road_ledger_cases=len(ledger))
    (output/'road_ledger.json').write_bytes(json_bytes(dict(roads=ledger, summary=summary,
        prior_ledger_sha256=file_hash(OLD_LEDGER), field_validation=False,
        complete_physical_path_support=False, refinement_convergence_proven=False,
        rigorous_noise_error_bound_db=None, all_inclusive_noise_db=None, map_updated=False)))
    report = dict(status='completed_ground_average_native_replay', counts=counts, summary=summary,
        finer_status=check['status'], finer_maximum_change_db=check['maximum_absolute_change_db'],
        elapsed_seconds=time.perf_counter()-start, field_validation=False, all_inclusive_noise_db=None, map_updated=False)
    (output/'report.json').write_bytes(json_bytes(report))
    assert all(file_hash(path) == value for path, value in hashes.items()), 'Executed implementation changed'
    for name, path in parents.items():
        helper.verified(path)
        assert file_hash(path/'inventory.json') == parent_hashes[name]
    code = output/'code'
    code.mkdir()
    for path in hashes:
        shutil.copyfile(path, code/path.name)
    generation = helper.seal(output, 'ground-average-native-replay-')
    print(json.dumps(dict(generation_id=generation, report=report)), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', required=True)
    run(parser.parse_args().output)
