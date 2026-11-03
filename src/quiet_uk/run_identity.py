"""Which code a calculation actually runs, and whether that code is acceptable.

v4 pinned 93 repository files, including the web server and unrelated tests, so
editing any of them blocked resume and sealing. A run now pins its import
closure: the repository files loaded by importing its engine script in a clean
interpreter. Engine code (no function-level imports of repository modules) is
fully covered by that closure.

A closure file is acceptable when it is byte-identical to the validation
receipt, or when an equivalence receipt shows the current files reproduced a
sample of saved reference observations byte for byte.
"""
import hashlib
import json
from pathlib import Path
import subprocess
import sys

MINIMUM_EQUIVALENCE_RECORDS = 1000

_PROBE = r"""
import importlib.util, json, sys
from pathlib import Path
root = Path(sys.argv[1]).resolve()
spec = importlib.util.spec_from_file_location('closure_probe', sys.argv[2])
module = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = module
spec.loader.exec_module(module)
names = set()
for value in list(sys.modules.values()):
    path = getattr(value, '__file__', None)
    if path and path.endswith('.py') and Path(path).resolve().is_relative_to(root):
        names.add(Path(path).resolve().relative_to(root).as_posix())
print(json.dumps(sorted(names)))
"""


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def import_closure(root, script):
    """sha256 of every repository .py file loaded by importing script."""
    root = Path(root).resolve()
    output = subprocess.run([sys.executable, '-I', '-c', _PROBE, str(root), str(Path(script).resolve())],
                            capture_output=True, text=True, check=True, cwd=root)
    names = json.loads(output.stdout.strip().splitlines()[-1])
    return {name: sha256(root / name) for name in names}


def unaccepted_files(closure, validated, equivalence=None):
    """Closure files neither validated nor covered by an identical replay."""
    covered = {}
    if (equivalence and equivalence.get('status') == 'identical'
            and equivalence.get('records', 0) >= MINIMUM_EQUIVALENCE_RECORDS):
        covered = equivalence.get('engine_import_closure_sha256', {})
    return sorted(name for name, digest in closure.items()
                  if validated.get(name) != digest and covered.get(name) != digest)
