"""Replay saved v4 observations with the current engine; require identical bytes.

Speed or memory changes to the pinned kernels are acceptable only when they
reproduce the recorded journal values exactly. This samples saved records across
roads (including withheld points), recalculates each with the current code via
script 119's own point functions, and compares the serialised journal lines.
The receipt records the engine's import closure, sample and timing.
"""
import argparse
from datetime import datetime, timezone
import gzip
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import random
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def load_engine(root):
    sys.path.insert(0, str(root / 'src'))
    spec = importlib.util.spec_from_file_location('engine_under_test', root / 'scripts/119_calculate_real_road_reflections.py')
    engine = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = engine
    spec.loader.exec_module(engine)
    return engine


def journal_records(directory):
    for path in sorted(directory.glob('chunk-*.jsonl.gz')):
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                yield json.loads(line)


def sample(output, count, seed):
    """Up to count records spread over roads; every held record found is kept."""
    rng = random.Random(seed)
    roads = sorted(p.parent.parent.name for p in output.glob('roads/*/observations/chunk-000001.jsonl.gz'))
    rng.shuffle(roads)
    per_road = max(1, math.ceil(count / max(1, len(roads))))
    chosen = []
    for link in roads:
        records = list(journal_records(output / 'roads' / link / 'observations'))
        held = [r for r in records if r['value']['status'] == 'withheld'][:1]
        picked = held + rng.sample(records, min(per_road, len(records)))
        chosen.extend((link, r) for r in picked)
        if len(chosen) >= count:
            break
    return chosen[:count]


def line(value):
    return json.dumps(value, separators=(',', ':'), allow_nan=False)


def evaluate(engine, state, link, xy):
    """Script 119 compute.evaluate body, without the journal."""
    policy = state['policies'][link]['physical_source']
    road = state['roads'][link]
    prepared = None
    try:
        prepared = state['builder'].prepare_point(xy, engine.RECEIVER, reflections=True)
        r = engine.road_point_energy(prepared, state['features'], engine.PHYSICAL, policy, engine.FACADE,
            lambda distance: engine.source_joint_power(road, state['coefficients'], state['calendar'], state['air'], distance))
        value = engine.thin_point(r)
        value['reflector_contact_normalizations'] = [dict(facet_id=f['facet_id'], changes=f['contact_normalization'])
            for f in prepared['reflections'] if f.get('contact_normalization')]
    except (ValueError, OverflowError, ZeroDivisionError) as exc:
        value = dict(status='withheld', reasons=['unsupported_prepared_geometry:' + str(exc)],
                     relative_energy_per_m=None, paths=[], scope=engine.SCOPE)
    return engine.plain(value)


def closure(root):
    """sha256 of every module file the engine imported from this checkout."""
    files = {}
    for module in list(sys.modules.values()):
        path = getattr(module, '__file__', None)
        if path and Path(path).resolve().is_relative_to(root) and Path(path).suffix == '.py':
            files[Path(path).resolve().relative_to(root).as_posix()] = hashlib.sha256(Path(path).read_bytes()).hexdigest()
    return dict(sorted(files.items()))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--engine-root', default=str(ROOT), help='Checkout whose engine code is tested')
    parser.add_argument('--reference', default=str(ROOT / 'artifacts/real_road_reflection_calculation_v4'))
    parser.add_argument('--count', type=int, default=200)
    parser.add_argument('--seed', type=int, default=20261026)
    parser.add_argument('--receipt', help='Write a JSON receipt here')
    args = parser.parse_args()
    root, reference = Path(args.engine_root).resolve(), Path(args.reference).resolve()
    for name in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[name] = '1'
    engine = load_engine(root)
    engine.initialise(str(reference))
    state = engine.prior.STATE
    fingerprint = engine.RUN['fingerprint']
    records = sample(reference, args.count, args.seed)
    mismatches, seconds = [], 0.
    for index, (link, record) in enumerate(records, 1):
        assert record['fingerprint'] == fingerprint
        started = time.perf_counter()
        value = evaluate(engine, state, link, record['xy_bng'])
        seconds += time.perf_counter() - started
        if line(value) != line(record['value']):
            mismatches.append(dict(link_id=link, xy_bng=record['xy_bng']))
        if index % 25 == 0:
            print(f'{index}/{len(records)} replayed; {len(mismatches)} mismatches; {seconds / index:.3f} s/point', flush=True)
    receipt = dict(status='identical' if not mismatches else 'mismatch', generated=datetime.now(timezone.utc).isoformat(),
        reference_fingerprint=fingerprint, reference=str(reference), seed=args.seed, records=len(records),
        withheld_records=sum(r['value']['status'] == 'withheld' for _, r in records), roads=len({l for l, _ in records}),
        mismatches=mismatches, seconds_per_point=seconds / max(1, len(records)), engine_import_closure_sha256=closure(root))
    print(json.dumps({k: v for k, v in receipt.items() if k != 'engine_import_closure_sha256'}, indent=2), flush=True)
    if args.receipt:
        Path(args.receipt).write_text(json.dumps(receipt, indent=2), encoding='utf-8')
    sys.exit(1 if mismatches else 0)


if __name__ == '__main__':
    main()
