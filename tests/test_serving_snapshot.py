"""Isolation, bounded exports and lifecycle of a regional reader."""
from concurrent.futures import ThreadPoolExecutor
import hashlib
import io
import json
from pathlib import Path
import threading
from urllib.error import HTTPError
from urllib.request import urlopen
import zipfile

import pytest

from quiet_uk import source_pilot as p
from quiet_uk.catalogue import CatalogueIntegrityError
from quiet_uk.explorer_server import ExplorerServer
from quiet_uk.serving_snapshot import RegionalSnapshot, member_path
from test_source_pilot import pilot, coords
from test_evidence_semantics import interpreted


@pytest.mark.parametrize('release_fixture', ['pilot', 'interpreted'])
def test_snapshot_preserves_evidence_without_rehashing(request, release_fixture, monkeypatch):
    source = request.getfixturevalue(release_fixture)
    points = [coords(source, col=col) for col in (0, 1, 2, 3)] + [(-3, 55)]
    expected = [source.lookup('heathrow', *point) for point in points]
    with RegionalSnapshot(source) as snapshot:
        root = snapshot.root
        assert root != source.root
        def no_hash(*args):
            pytest.fail('Warm requests must not hash the release files')
        monkeypatch.setattr(p, 'file_hash', no_hash)
        # Both the files and original in-memory manifest are independent.
        (source.root/source.manifest['records'][0]['path']).write_bytes(b'changed')
        source.manifest['sites'].clear()
        for point, result in zip(points, expected):
            assert snapshot.lookup('heathrow', *point) == result
        assert snapshot.locate(*points[0]) == ['heathrow']
    assert not root.parent.exists()


def test_corruption_before_snapshot_fails_and_cleans_partial_copy(pilot, tmp_path):
    (pilot.root/pilot.manifest['records'][0]['path']).write_bytes(b'corrupt')
    cache = tmp_path/'cache'; cache.mkdir()
    with pytest.raises(CatalogueIntegrityError, match='checksum'):
        RegionalSnapshot(pilot, directory=cache)
    assert list(cache.iterdir()) == []


def test_accidental_snapshot_changes_fail_closed(pilot):
    with RegionalSnapshot(pilot) as snapshot:
        name = snapshot.manifest['records'][0]['path']
        private = snapshot.verified_path(name)
        private.chmod(0o600)
        private.write_bytes(b'changed')
        with pytest.raises(CatalogueIntegrityError, match='changed'):
            snapshot.lookup('heathrow', -0.45, 51.46)
        with pytest.raises(CatalogueIntegrityError):
            snapshot.bundle_path()
        assert not (snapshot.root.parent/'evidence.zip').exists()
        assert not (snapshot.root.parent/'evidence.partial').exists()


def test_concurrent_lookups_with_eviction_bound_open_readers(pilot, monkeypatch):
    points = [coords(pilot, col=col) for col in (0, 1, 2, 3)]
    expected = [pilot.lookup('heathrow', *point) for point in points]
    real_open = p.rasterio.open
    active, peak = set(), []
    guard = threading.Lock()
    class Tracked:
        def __init__(self, path):
            self.reader = real_open(path)
            with guard:
                active.add(id(self)); peak.append(len(active))
        def __getattr__(self, name):
            return getattr(self.reader, name)
        def close(self):
            self.reader.close()
            with guard:
                active.discard(id(self))
    monkeypatch.setattr(p.rasterio, 'open', Tracked)
    with RegionalSnapshot(pilot, max_readers=2) as snapshot:
        with ThreadPoolExecutor(max_workers=5) as pool:
            results = list(pool.map(lambda i: snapshot.lookup('heathrow', *points[i % 4]), range(20)))
        assert results == [expected[i % 4] for i in range(20)]
        assert max(peak) <= 2
    assert not active


def test_reader_is_released_on_lookup_error(pilot):
    name = pilot.manifest['records'][0]['path']
    with RegionalSnapshot(pilot, max_readers=1) as snapshot:
        with pytest.raises(RuntimeError, match='lookup failed'):
            with snapshot.open_raster(name):
                raise RuntimeError('lookup failed')
        with snapshot.open_raster(name) as reader:
            assert not reader.closed
    assert reader.closed


def test_failed_export_can_retry_without_reusing_partial_bytes(pilot, monkeypatch):
    with RegionalSnapshot(pilot) as snapshot:
        original = snapshot.write_bundle
        def fail(output):
            output.write_bytes(b'partial')
            raise OSError('Disk write failed')
        monkeypatch.setattr(snapshot, 'write_bundle', fail)
        with pytest.raises(OSError, match='Disk write failed'):
            snapshot.bundle_path()
        assert not (snapshot.root.parent/'evidence.partial').exists()
        monkeypatch.setattr(snapshot, 'write_bundle', original)
        with zipfile.ZipFile(snapshot.bundle_path()) as archive:
            assert archive.testzip() is None


def test_concurrent_archive_is_built_once_and_reproduces_every_member(pilot, monkeypatch):
    with RegionalSnapshot(pilot) as snapshot:
        build = snapshot.write_bundle
        calls = []
        def counted(output):
            calls.append(output)
            build(output)
        monkeypatch.setattr(snapshot, 'write_bundle', counted)
        # No entire source file or ZIP may be read into memory by the writer.
        monkeypatch.setattr(Path, 'read_bytes', lambda *a: pytest.fail('Whole-file read'))
        with ThreadPoolExecutor(max_workers=5) as workers:
            paths = list(workers.map(lambda _: snapshot.bundle_path(), range(10)))
        assert len(calls) == 1 and len(set(paths)) == 1
        with zipfile.ZipFile(paths[0]) as archive:
            assert set(archive.namelist()) == {'manifest.json', *snapshot.manifest['files']}
            assert json.loads(archive.read('manifest.json')) == snapshot.manifest
            for name, digest in snapshot.manifest['files'].items():
                assert hashlib.sha256(archive.read(name)).hexdigest() == digest
        # Corrupt cached downloads are never reused.
        paths[0].chmod(0o600)
        with paths[0].open('ab') as output:
            output.write(b'corrupt')
        with pytest.raises(CatalogueIntegrityError, match='archive changed'):
            snapshot.bundle_path()


def test_http_archive_and_download_limit_leave_point_queries_available(pilot, monkeypatch):
    server = ExplorerServer(0, None, Path(__file__).parents[1]/'explorer', pilot=pilot)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    base = f'http://127.0.0.1:{server.server_port}'
    monkeypatch.setattr(RegionalSnapshot, 'bundle', lambda *a: pytest.fail('In-memory export'))
    try:
        server.download_slots.acquire(); server.download_slots.acquire()
        try:
            with pytest.raises(HTTPError) as caught:
                urlopen(base+'/downloads/pilot.zip')
            assert caught.value.code == 503
            assert json.load(urlopen(base+'/api/pilot/location?site=heathrow&lon=-0.45&lat=51.46'))['release_id'] == pilot.manifest['release_id']
        finally:
            server.download_slots.release(); server.download_slots.release()
        with urlopen(base+'/downloads/pilot.zip') as response:
            payload = response.read()
            assert len(payload) == int(response.headers['Content-Length'])
            assert pilot.manifest['release_id'] in response.headers['Content-Disposition']
            with zipfile.ZipFile(io.BytesIO(payload)) as archive:
                assert json.loads(archive.read('manifest.json')) == pilot.manifest
    finally:
        server.shutdown(); server.server_close(); worker.join(5)
    assert not server.pilot.root.parent.exists()


def test_server_close_waits_for_reader_before_cleanup(pilot, monkeypatch):
    server = ExplorerServer(0, None, Path(__file__).parents[1]/'explorer', pilot=pilot)
    entered, release, closed = threading.Event(), threading.Event(), threading.Event()
    original = server.pilot.lookup
    def delayed(*args):
        entered.set()
        assert release.wait(10)
        return original(*args)
    monkeypatch.setattr(server.pilot, 'lookup', delayed)
    worker = threading.Thread(target=server.serve_forever, daemon=True); worker.start()
    def close():
        server.shutdown(); server.server_close(); closed.set()
    with ThreadPoolExecutor(max_workers=2) as pool:
        response = pool.submit(lambda: json.load(urlopen(f'http://127.0.0.1:{server.server_port}/api/pilot/location?site=heathrow&lon=-0.45&lat=51.46')))
        try:
            assert entered.wait(5)
            closing = pool.submit(close)
            assert not closed.wait(0.2)
            assert server.pilot.root.exists()
        finally:
            release.set()
        assert response.result(timeout=10)['release_id'] == pilot.manifest['release_id']
        closing.result(timeout=10)
    worker.join(5)
    assert not server.pilot.root.parent.exists()


@pytest.mark.parametrize('name', ['../escape', '/absolute', 'a/../../escape', 'a\\escape', 'C:drive', 'manifest.json', 'a//b', '.'])
def test_nonportable_or_escaping_member_names_are_rejected(tmp_path, name):
    with pytest.raises(CatalogueIntegrityError):
        member_path(tmp_path, name)
