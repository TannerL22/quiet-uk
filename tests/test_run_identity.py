"""A run pins exactly the code its engine imports; changed code needs identical replay."""
from quiet_uk import run_identity


def fake_repository(tmp_path):
    (tmp_path / 'pkg').mkdir()
    (tmp_path / 'pkg/__init__.py').write_text('')
    (tmp_path / 'pkg/used.py').write_text('VALUE = 1\n')
    (tmp_path / 'pkg/unused.py').write_text('VALUE = 2\n')
    (tmp_path / 'engine.py').write_text(
        'import sys\nfrom pathlib import Path\nsys.path.insert(0, str(Path(__file__).parent))\nimport pkg.used\n')
    return tmp_path


def test_closure_is_imported_repository_code_only(tmp_path):
    root = fake_repository(tmp_path)
    closure = run_identity.import_closure(root, root / 'engine.py')
    assert sorted(closure) == ['engine.py', 'pkg/__init__.py', 'pkg/used.py']
    assert closure['pkg/used.py'] == run_identity.sha256(root / 'pkg/used.py')


def test_unrelated_files_never_block_a_run(tmp_path):
    root = fake_repository(tmp_path)
    before = run_identity.import_closure(root, root / 'engine.py')
    (root / 'pkg/unused.py').write_text('VALUE = 3\n')
    assert run_identity.import_closure(root, root / 'engine.py') == before


def test_changed_engine_code_needs_an_identical_replay():
    closure = {'a.py': 'new', 'b.py': 'same', 'c.py': 'added'}
    validated = {'a.py': 'old', 'b.py': 'same', 'z.py': 'irrelevant'}
    assert run_identity.unaccepted_files(closure, validated) == ['a.py', 'c.py']
    replay = dict(status='identical', records=run_identity.MINIMUM_EQUIVALENCE_RECORDS,
                  engine_import_closure_sha256={'a.py': 'new', 'c.py': 'added'})
    assert run_identity.unaccepted_files(closure, validated, replay) == []
    for weaker in (dict(replay, status='mismatch'), dict(replay, records=10),
                   dict(replay, engine_import_closure_sha256={'a.py': 'other', 'c.py': 'added'})):
        assert run_identity.unaccepted_files(closure, validated, weaker)
