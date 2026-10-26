"""Read-only estimate of which roads the pinned v4 model must withhold.

At each road's exact first coordinates (the engine's own base-panel order), run
only the facade candidate search and the engine's own facade geometry checks:
no terrain profiles, ground sampling or acoustics. A finding is certain: that
point is withheld, so the engine stops the road at that base panel. No finding
does not predict acceptance; profile, obstacle and finer-pass holds stay possible.

The calculation monitor reads the result to order work and to stop calculating
a road's coordinates past its certain hold. Nothing in the run is changed.
"""
import argparse
from concurrent.futures import ProcessPoolExecutor
from datetime import datetime, timezone
import gzip
import importlib.util
import json
import math
import os
from pathlib import Path
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'src'))
from quiet_uk import calculation_scheduling as plan  # noqa: E402
from quiet_uk.conditional_road_reflections import facade_inputs  # noqa: E402
from quiet_uk.reflection_path import vector  # noqa: E402

ENGINE = None


def load_engine():
    global ENGINE
    if ENGINE is None:
        spec = importlib.util.spec_from_file_location('pinned_road_calculation', ROOT / 'scripts/119_calculate_real_road_reflections.py')
        ENGINE = importlib.util.module_from_spec(spec)
        sys.modules[spec.name] = ENGINE
        spec.loader.exec_module(ENGINE)
    return ENGINE


def initialise(output):
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[name] = '1'
    load_engine().initialise(output)


def certain_point_hold(engine, state, xy):
    """Facade reasons the engine must give this point, or [] when none is certain."""
    builder = state['builder']
    xy = vector(xy, 2)
    search = builder.reflection_candidates(xy, engine.RECEIVER)
    if search['reason'] == 'reflection_search_budget_exceeded':
        return ['reflection_search_budget_exceeded']
    reasons = []
    for candidate in search['candidates']:
        hit = candidate['plan']['reflection_xy']
        # As in RoadSceneBuilder.prepare_point; an empty crossing list only skips
        # the profile-dependent footprint-adjacency check.
        ref = dict(candidate, profile=dict(crossings=[]),
                   wall=dict(unfolded_x_m=math.dist(xy, hit), base_z_m=builder.terrain.sample(hit)['terrain_odn_m']))
        reasons += facade_inputs(ref, state['features'], engine.PHYSICAL, engine.FACADE)['geometry_blockers']
    return sorted(set(reasons))


def coarse_to_fine(count):
    """Visit every index once, widely spaced first, so a hold anywhere is found early."""
    seen = set()
    for stride in (256, 64, 16, 4, 1):
        for index in range(0, count, stride):
            if index not in seen:
                seen.add(index)
                yield index


def estimate_road(task):
    """Any certain hold proves the road is withheld at or before that base panel."""
    engine = ENGINE
    state = engine.prior.STATE
    started = time.perf_counter()
    source, partition = task['source'], task['partition']
    base, _ = plan.primary_plan(source, partition, engine.DEFAULTS['maximum_panel_m'])
    budget = task.get('max_checks') or len(base)
    checked = 0
    for checked, index in enumerate(coarse_to_fine(len(base)), 1):
        if checked > budget:
            checked -= 1
            break
        reasons = certain_point_hold(engine, state, base[index])
        if reasons:
            return dict(link_id=source['link_id'], planned_primary_points=len(base), checked_points=checked,
                        complete_scan=False, certain_hold_index=index, reasons=reasons, seconds=time.perf_counter() - started)
    return dict(link_id=source['link_id'], planned_primary_points=len(base), checked_points=checked,
                complete_scan=checked == len(base), certain_hold_index=None, reasons=[], seconds=time.perf_counter() - started)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', default=str(ROOT / 'artifacts/real_road_reflection_calculation_v4'))
    parser.add_argument('--control', default=str(ROOT / 'artifacts/local_calculation_control'))
    parser.add_argument('--workers', type=int, default=4)
    parser.add_argument('--include-completed', action='store_true',
                        help='Also estimate saved roads and compare with their actual engine status')
    parser.add_argument('--roads', help='Comma-separated road IDs (default: all eligible)')
    parser.add_argument('--max-checks', type=int, help='Check at most this many coordinates per road, '
                        'widely spaced first; holds found are still certain, so the count is a lower bound')
    args = parser.parse_args()
    output = Path(args.output).resolve()
    engine = load_engine()
    _, protocol = engine.prepare(output, True)
    workload = json.loads((output / 'workload_plan.json').read_bytes())
    assert workload['fingerprint'] == protocol['fingerprint']
    sources = {r['link_id']: r for r in json.loads((output / 'source_lines.json').read_bytes())['source_roads']}
    done = {p.parent.name: json.loads(p.read_bytes())['status'] for p in (output / 'roads').glob('*/result.json')}
    selected = set(args.roads.split(',')) if args.roads else None
    rows = [r for r in workload['roads'] if (args.include_completed or r['link_id'] not in done)
            and (selected is None or r['link_id'] in selected)]
    rows.sort(key=lambda r: -r['minimum_primary_observations'])

    def tasks():
        for row in rows:
            record = json.loads(gzip.decompress((output / 'source_partitions' / (row['link_id'] + '.json.gz')).read_bytes()))
            assert record['fingerprint'] == protocol['fingerprint']
            yield dict(source=sources[row['link_id']], partition=record['partition'], max_checks=args.max_checks)

    started = time.perf_counter()
    results = {}
    with ProcessPoolExecutor(max_workers=args.workers, initializer=initialise, initargs=(str(output),)) as pool:
        for count, result in enumerate(pool.map(estimate_road, tasks()), 1):
            results[result['link_id']] = result
            if count % 25 == 0 or count == len(rows):
                print(f'{count}/{len(rows)} roads checked; {time.perf_counter() - started:.0f}s', flush=True)

    planned = {r['link_id']: r for r in workload['roads']}
    remaining = [link for link in results if link not in done]
    held = [link for link in remaining if results[link]['certain_hold_index'] is not None]
    summary = dict(
        remaining_roads_checked=len(remaining), remaining_certain_holds=len(held),
        remaining_roads_fully_scanned=sum(results[l]['complete_scan'] for l in remaining if l not in held),
        max_checks_per_road=args.max_checks,
        remaining_planned_observations=sum(planned[l]['minimum_primary_plus_finer_observations'] for l in remaining),
        remaining_planned_observations_on_certain_holds=sum(planned[l]['minimum_primary_plus_finer_observations'] for l in held),
        observations_needed_by_certain_holds=sum((results[l]['certain_hold_index'] // 5 + 1) * 5 for l in held),
        reason_counts={})
    for link in held:
        for reason in results[link]['reasons']:
            summary['reason_counts'][reason] = summary['reason_counts'].get(reason, 0) + 1
    validation = None
    completed = [link for link in results if link in done]
    if completed:
        predicted = [link for link in completed if results[link]['certain_hold_index'] is not None]
        contradictions = [link for link in predicted if not done[link].startswith('withheld')]
        validation = dict(completed_roads_checked=len(completed), predicted_holds=len(predicted),
                          actual_withheld=sum(done[l].startswith('withheld') for l in completed),
                          predicted_holds_actually_withheld=len(predicted) - len(contradictions),
                          contradictions=contradictions)
        if contradictions:
            print('WARNING: predicted certain holds that the engine accepted: ' + ', '.join(contradictions), flush=True)
    report = dict(fingerprint=protocol['fingerprint'], generated=datetime.now(timezone.utc).isoformat(),
                  method='Facade candidate search and engine facade checks at exact base-panel coordinates, '
                         'widely spaced first; read-only. Holds are certain; their count is a lower bound.',
                  summary=summary, validation=validation, roads=results, elapsed_seconds=time.perf_counter() - started)
    control = Path(args.control)
    control.mkdir(parents=True, exist_ok=True)
    temporary = control / 'yield_estimate.json.writing'
    temporary.write_text(json.dumps(report, indent=2, allow_nan=False), encoding='utf-8')
    os.replace(temporary, control / 'yield_estimate.json')
    print(json.dumps(dict(summary=summary, validation=validation), indent=2), flush=True)


if __name__ == '__main__':
    main()
