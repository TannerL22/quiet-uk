"""Recover complete checkpointed roads after restart without changing physics.

Unfinished cases restart from their independently reviewed historical seeds.
Recovered complete rows remain provisional until the full independent review.
"""
import argparse
from collections import Counter
import copy
import gzip
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import zlib

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('budget_restart_pipeline', ROOT/'scripts/109_integrate_budget_held_roads.py')
pipeline = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = pipeline
spec.loader.exec_module(pipeline)
read = pipeline.read
file_hash = pipeline.file_hash
json_bytes = pipeline.json_bytes


def recover(inputs, output):
    inputs, output = Path(inputs).resolve(), Path(output).resolve()
    if not inputs.is_relative_to(ROOT/'artifacts') or (inputs/'inventory.json').exists():
        raise ValueError('An interrupted unsealed workspace generation is required')
    if output.exists() or not output.is_relative_to(ROOT/'artifacts'):
        raise ValueError('Fresh workspace recovery required')
    pipeline.verified_model()
    original_protocol = read(inputs/'protocol.json')
    assert original_protocol['kind'] == 'budget_road_integration_v1'
    observed = ROOT/'artifacts'/original_protocol['budget_observation_directory']
    audit = ROOT/'artifacts'/original_protocol['budget_observation_review_directory']
    for parent in (observed, audit):
        pipeline.helper.verified(parent)
        assert file_hash(parent/'inventory.json') == original_protocol['parent_inventory_sha256'][parent.name]
    assert original_protocol['model_validation_sha256'] == file_hash(pipeline.MODEL_RECEIPT)
    assert read(audit/'report.json')['input_inventory_sha256'] == file_hash(observed/'inventory.json')
    # This exact controller and every numerical dependency were captured before
    # the interrupted run; the current model receipt pins the physics separately.
    for path in pipeline.code_paths():
        assert file_hash(path) == file_hash(observed/'code'/path.name)
    rows = read(inputs/'completed_roads.json')['roads']
    assert rows and len(rows) < len(original_protocol['selected_identities'])
    assert [pipeline.key(r) for r in rows] == [pipeline.key(r) for r in original_protocol['selected_identities'][:len(rows)]]
    seeds = read(observed/'seed_evaluations.json')
    assert read(inputs/'seed_evaluations.json') == seeds
    source_map = {r['link_id']: r for r in read(ROOT/'artifacts/real_road_scenes_v2/source_lines.json')['source_roads']}
    hashes = {p: file_hash(p) for p in [inputs/'protocol.json', inputs/'completed_roads.json', inputs/'node_evaluations.jsonl.gz']}
    # Text/Gzip flushes commit complete cases, although a hard restart prevents
    # writing the final gzip footer. Accept only a complete JSON-line prefix
    # whose exact record counts agree with the separate completed-road journal.
    decoder = zlib.decompressobj(31)
    payload = decoder.decompress((inputs/'node_evaluations.jsonl.gz').read_bytes())+decoder.flush()
    assert payload.endswith(b'\n'), 'Uncommitted partial JSON record'
    nodes = [json.loads(line) for line in payload.splitlines()]
    assert len(nodes) == sum(r['evaluation_count'] for r in rows)
    counts = Counter()
    output.mkdir(parents=True)
    (output/'point_scenes').mkdir()
    cursor = 0
    with gzip.open(output/'node_evaluations.jsonl.gz', 'wt', encoding='utf-8', compresslevel=1) as target:
        for row in rows:
            identity = pipeline.key(row)
            prior = seeds[identity]
            cells = {c['segment_id']: c for c in source_map[row['link_id']]['source_cells']}
            relative = 'point_scenes/'+identity.replace('|', '__')+'.jsonl.gz'
            scene_path = inputs/relative
            hashes[scene_path] = file_hash(scene_path)
            completed = read(inputs/'case_progress'/(identity.replace('|', '__')+'.json'))
            assert completed['complete'] and completed['identity'] == identity
            assert completed['new_native_evaluations'] == row['new_evaluation_count']
            assert row['new_evaluation_count'] == row['evaluation_count']-len(prior)
            assert row['controls'] == original_protocol['case_controls'][identity]
            seen = set()
            with gzip.open(scene_path, 'rt', encoding='utf-8') as scenes:
                for ni in range(row['evaluation_count']):
                    node = dict(nodes[cursor]); cursor += 1
                    assert pipeline.key(node) == identity and node['node_id'] == ni
                    point = pipeline.point_key(node)
                    assert point not in seen
                    seen.add(point)
                    cell = cells[node['source_cell_id']]
                    t = node['fraction']
                    assert 0 < t < 1
                    assert node['xy_bng'] == [a+(b-a)*t for a, b in zip(cell['start_xy_bng'], cell['end_xy_bng'])]
                    if ni < len(prior):
                        assert node['evaluation_origin'] == 'verified_prior_generation'
                        assert all(node[k] == v for k, v in prior[ni].items() if k not in ('node_id', 'evaluation_origin'))
                    else:
                        assert node['evaluation_origin'] == 'new_geographic_evaluation'
                        reference = dict(directory=inputs.name, path=relative, record_id=ni-len(prior))
                        assert node['kernel_scene_reference'] == reference
                        captured = json.loads(next(scenes))
                        assert captured['record_id'] == reference['record_id']
                        assert captured['xy_bng'] == node['xy_bng']
                        assert captured['native_profile_sha256'] == node['native_profile_sha256']
                        node['kernel_scene_reference'] = dict(reference, directory=output.name)
                        counts['complete_native_scene_records_recovered'] += 1
                    target.write(json.dumps(node, allow_nan=False, separators=(',', ':'))+'\n')
                    counts['point_records_recovered'] += 1
                assert next(scenes, None) is None  # Includes gzip footer/CRC verification.
            shutil.copyfile(scene_path, output/relative)
            counts['complete_road_cases_recovered'] += 1
    assert cursor == len(nodes)
    assert all(file_hash(p) == sha for p, sha in hashes.items()), 'Interrupted evidence changed while captured'
    (output/'completed_roads.json').write_bytes(json_bytes(dict(roads=rows)))
    (output/'protocol.json').write_bytes(json_bytes(dict(kind='recovered_budget_primary_prefix_v1',
        interrupted_directory=inputs.name, original_protocol=original_protocol,
        model_validation_sha256=file_hash(pipeline.MODEL_RECEIPT),
        parent_inventory_sha256={p.name: file_hash(p/'inventory.json') for p in (observed, audit)},
        interrupted_evidence_sha256={str(p.relative_to(inputs)).replace('\\', '/'): sha for p, sha in hashes.items()},
        complete_cases_only=True, partial_case_values_reused=False)))
    (output/'report.json').write_bytes(json_bytes(dict(status='captured_completed_budget_primary_prefix',
        counts=counts, interrupted_gzip_had_final_footer=decoder.eof,
        numerical_review_pending=True, field_validation=False, all_inclusive_noise_db=None)))
    (output/'code').mkdir()
    shutil.copyfile(__file__, output/'code'/Path(__file__).name)
    generation = pipeline.helper.seal(output, 'recovered-budget-primary-')
    print(json.dumps(dict(generation_id=generation, counts=counts)), flush=True)


def install_recovery(recovery, mode):
    recovery = Path(recovery).resolve()
    pipeline.helper.verified(recovery)
    protocol = read(recovery/'protocol.json')
    assert protocol['kind'] == 'recovered_budget_primary_prefix_v1'
    assert protocol['model_validation_sha256'] == file_hash(pipeline.MODEL_RECEIPT)
    assert file_hash(Path(__file__)) == file_hash(recovery/'code'/Path(__file__).name)
    rows = read(recovery/'completed_roads.json')['roads']
    recovered_nodes = {pipeline.key(r): [] for r in rows}
    with gzip.open(recovery/'node_evaluations.jsonl.gz', 'rt', encoding='utf-8') as stream:
        for line in stream:
            node = json.loads(line)
            recovered_nodes[pipeline.key(node)].append(node)
    original_prepare = pipeline.prepare_directory
    original_bounded = pipeline.bounded
    original_paths = pipeline.code_paths
    original_finish = pipeline.finish
    state = {}

    def code_paths():
        return [*original_paths(), Path(__file__)]

    def prepare_directory(output, parents):
        output, hashes, codes = original_prepare(output, [*parents, recovery.name])
        state['output'] = output
        if mode == 'integrate':
            for source in (recovery/'point_scenes').glob('*.jsonl.gz'):
                shutil.copyfile(source, output/'point_scenes'/source.name)
        return output, hashes, codes

    def bounded(pool, jobs):
        jobs = list(jobs)
        assert [(s['link_id'], case) for s, case in jobs[:len(rows)]] == [(r['link_id'], r['physical_case_id']) for r in rows]
        for row in rows:
            result = copy.deepcopy(row)
            result['evaluations'] = copy.deepcopy(recovered_nodes[pipeline.key(row)])
            for node in result['evaluations']:
                reference = node.get('kernel_scene_reference')
                if reference and reference['directory'] == recovery.name:
                    node['kernel_scene_reference'] = dict(reference, directory=state['output'].name)
            print(f'Recovered completed case: {pipeline.key(row)} ({row["evaluation_count"]} observations)', flush=True)
            yield result
        yield from original_bounded(pool, jobs[len(rows):])

    def finish(output, parents, codes, prefix):
        report = read(output/'report.json')
        report.update(recovery_generation=read(recovery/'inventory.json')['generation_id'],
            complete_primary_cases_reused=len(rows) if mode == 'integrate' else 0,
            recovered_primary_observations=sum(r['evaluation_count'] for r in rows) if mode == 'integrate' else 0)
        (output/'report.json').write_bytes(json_bytes(report))
        original_finish(output, parents, codes, prefix)

    pipeline.code_paths = code_paths
    pipeline.prepare_directory = prepare_directory
    pipeline.finish = finish
    if mode == 'integrate':
        pipeline.bounded = bounded


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stage', choices=('recover', 'integrate', 'finer', 'spawn-probe'), required=True)
    parser.add_argument('--inputs')
    parser.add_argument('--output')
    parser.add_argument('--review')
    parser.add_argument('--recovery')
    args = parser.parse_args()
    if args.stage == 'recover':
        recover(args.inputs, args.output)
    elif args.stage == 'spawn-probe':
        pipeline.spawn_probe()
    else:
        install_recovery(args.recovery, args.stage)
        if args.stage == 'integrate':
            pipeline.integrate(args.inputs, args.review, args.output)
        else:
            pipeline.finer(args.inputs, args.review, args.output)
