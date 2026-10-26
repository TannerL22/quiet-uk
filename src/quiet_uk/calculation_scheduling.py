"""Scheduling helpers for the pinned road calculation; no acoustic logic.

The unchanged adaptive integration always requests the same first coordinates:
five interior positions on every base panel, then four on every accepted leaf.
Reproducing those exact floats lets independent processes calculate them early.
Their records use the original journal format, so the engine later reuses them
as ordinary cached observations. Values are only ever produced by the engine.
"""
from __future__ import annotations

import gzip
import json
import math
import os
from pathlib import Path

# Measured on v4: about 31 KB per cached observation, plus node lists and the
# deep copy made while writing node files.
OBSERVATION_BYTES = 70 * 1024


def coordinate_key(xy):
    """Exact-XY identity used by the engine's PointJournal."""
    return tuple(float(v).hex() for v in xy)


def primary_plan(source, partition, maximum_panel_m):
    """Base-panel coordinates in the engine's evaluation order, and base leaves.

    Mirrors integrate_adaptive's base partition and panel fractions exactly;
    tests compare it against the engine itself.
    """
    cells = source['source_cells']
    base = []
    for ci, cell in enumerate(cells):
        cuts = [v['fraction'] for v in partition['cells'][cell['segment_id']]]
        for a, b in zip(cuts, cuts[1:]):
            count = max(1, math.ceil((b-a)*cell['length_m']/maximum_panel_m))
            base.extend((ci, a+(b-a)*j/count, a+(b-a)*(j+1)/count) for j in range(count))
    points, seen, leaves = [], set(), []
    for ci, left, right in base:
        cell = cells[ci]; a, b = cell['start_xy_bng'], cell['end_xy_bng']
        mid = (left+right)/2
        for t in (mid, (3*left+right)/4, (left+3*right)/4,
                  mid-(right-left)/(2*math.sqrt(3)), mid+(right-left)/(2*math.sqrt(3))):
            if (ci, t) in seen:
                continue
            seen.add((ci, t))
            points.append([x+(y-x)*t for x, y in zip(a, b)])
        leaves.append(dict(source_cell_id=cell['segment_id'], left_fraction=left, right_fraction=right))
    return points, leaves


def finer_plan(source, leaves):
    """Four interior positions per leaf, exactly as script 119's finer pass."""
    cell_lookup = {c['segment_id']: c for c in source['source_cells']}
    points = []
    for leaf in leaves:
        cell = cell_lookup[leaf['source_cell_id']]
        for j in range(4):
            t = leaf['left_fraction']+(leaf['right_fraction']-leaf['left_fraction'])*(j+.5)/4
            points.append([a+(b-a)*t for a, b in zip(cell['start_xy_bng'], cell['end_xy_bng'])])
    return points


def truncate_to_panel(points, index):
    """Keep every point up to the end of the base panel containing index."""
    return points[:min(len(points), (index//5+1)*5)]


def journal_records(directory):
    """Read-only replay of closed chunks and complete active-journal lines."""
    directory = Path(directory)
    for path in sorted(directory.glob('chunk-*.jsonl.gz')):
        with gzip.open(path, 'rt', encoding='utf-8') as stream:
            for line in stream:
                yield json.loads(line)
    active = directory/'active.jsonl'
    if active.exists():
        for line in active.read_bytes().splitlines(keepends=True):
            if not line.endswith(b'\n'):
                break
            yield json.loads(line)


def journal_keys(directory, fingerprint):
    keys = set()
    for record in journal_records(directory):
        if record['fingerprint'] != fingerprint:
            raise ValueError('Saved observation belongs to another calculation')
        keys.add(coordinate_key(record['xy_bng']))
    return keys


def journal_line(record):
    """Byte format of PointJournal.store/rotate."""
    return (json.dumps(record, separators=(',', ':'), allow_nan=False)+'\n').encode()


def write_staged(path, records):
    """Atomically write one complete staged chunk in the closed-journal format."""
    path = Path(path); temporary = path.with_name(path.name+'.writing')
    with temporary.open('wb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0, compresslevel=1) as stream:
            for record in records:
                stream.write(journal_line(record))
    os.replace(temporary, path)


def merge_staged(staging, observations):
    """Move staged chunks into a road journal as new closed chunks.

    Call only while no PointJournal is open on that road. Returns files moved.
    """
    staging, observations = Path(staging), Path(observations)
    files = sorted(staging.glob('*.jsonl.gz')) if staging.exists() else []
    if not files:
        return 0
    observations.mkdir(parents=True, exist_ok=True)
    sequence = max((int(p.name.split('-')[1].split('.')[0]) for p in observations.glob('chunk-*.jsonl.gz')), default=0)
    for path in files:
        sequence += 1
        destination = observations/f'chunk-{sequence:06d}.jsonl.gz'
        if destination.exists():
            raise ValueError('Journal chunk already exists: '+destination.name)
        os.replace(path, destination)
    return len(files)


def witness_name(phase, index):
    """Staged witness files sort in engine evaluation order."""
    return f"{'0' if phase == 'base' else '1'}-{index:09d}.witness.json.gz"


def promote_first_witness(staging, road_directory):
    """Place the engine's first-held-profile witness before its road call.

    The engine writes the witness for the first held point it evaluates, unless
    one exists. Every point before the earliest staged hold was calculated, so
    that hold is the one the serial engine would have written.
    """
    staging, target = Path(staging), Path(road_directory)/'first_held_profile.json.gz'
    staged = sorted(staging.glob('*.witness.json.gz')) if staging.exists() else []
    if target.exists() or not staged:
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    os.replace(staged[0], target)
    return True


def chunks(points, size):
    return [points[i:i+size] for i in range(0, len(points), size)]
