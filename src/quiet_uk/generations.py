"""Immutable artifact generations: verify an inventory, or seal a finished output.

Formerly defined in scripts/57_acquire_ground_cover_sources.py, which other
scripts loaded by file path; that script now re-exports these functions.
"""
import hashlib
import json
from pathlib import Path

from quiet_uk.digests import file_hash, json_bytes

ROOT = Path(__file__).resolve().parents[2]


def artifacts_root():
    # Resolved, so a linked artifacts folder (e.g. in a git worktree) still matches.
    return (ROOT / 'artifacts').resolve()


def verified(directory):
    directory = Path(directory).resolve()
    if not directory.is_relative_to(artifacts_root()):
        raise ValueError('Inputs must be workspace artifacts')
    inventory = json.loads((directory / 'inventory.json').read_bytes())
    for name, digest in inventory['files'].items():
        p = (directory / name).resolve()
        if not p.is_relative_to(directory) or file_hash(p) != digest:
            raise ValueError('Input integrity failed: ' + name)
    return inventory


def seal(output, prefix):
    files = {p.relative_to(output).as_posix(): file_hash(p)
             for p in sorted(output.rglob('*')) if p.is_file()}
    inventory = dict(generation_id=prefix + hashlib.sha256(json_bytes(files)).hexdigest()[:20], files=files)
    (output / 'inventory.json').write_bytes(json_bytes(inventory))
    return inventory['generation_id']
