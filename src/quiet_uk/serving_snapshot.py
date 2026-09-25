"""Private, verified regional copies owned by one running server.

The source directory may be replaced or edited after startup. Requests only use
the private copy. Fingerprints detect accidental changes to that copy; they are
not a security boundary against a process running as the same OS user.
"""
from __future__ import annotations

import copy
from collections import OrderedDict
from contextlib import contextmanager
import hashlib
from pathlib import Path, PurePosixPath
import stat
import tempfile
import threading

import rasterio

from .catalogue import CatalogueIntegrityError
from .explorer import json_bytes
from .source_pilot import SourcePilot

COPY_CHUNK = 1024 * 1024


class RasterReaders:
    """Bounded LRU; an individual GDAL reader is never used concurrently."""

    def __init__(self, limit):
        if limit < 1:
            raise ValueError('Reader limit must be positive')
        self.limit = limit
        self._entries = OrderedDict()
        self._condition = threading.Condition()
        self._closed = False

    @contextmanager
    def open(self, path):
        with self._condition:
            while True:
                if self._closed:
                    raise CatalogueIntegrityError('Raster readers are closed')
                if path in self._entries:
                    reader, busy = self._entries[path]
                    if not busy:
                        break
                else:
                    if len(self._entries) >= self.limit:
                        idle = next((key for key, (_, busy) in self._entries.items() if not busy), None)
                        if idle is not None:
                            self._entries.pop(idle)[0].close()
                    if len(self._entries) < self.limit:
                        reader = rasterio.open(path)
                        break
                self._condition.wait()
            self._entries[path] = (reader, True)
            self._entries.move_to_end(path)
        try:
            yield reader
        finally:
            with self._condition:
                self._entries[path] = (reader, False)
                self._condition.notify_all()

    def close(self):
        with self._condition:
            self._closed = True
            self._condition.notify_all()
            while any(busy for _, busy in self._entries.values()):
                self._condition.wait()
            for reader, _ in self._entries.values():
                reader.close()
            self._entries.clear()


def fingerprint(path):
    value = path.stat()
    if not stat.S_ISREG(value.st_mode):
        raise CatalogueIntegrityError('Snapshot member is not a regular file')
    return (value.st_dev, value.st_ino, value.st_size,
            value.st_mtime_ns, value.st_ctime_ns)


def member_path(root, name):
    """Require canonical portable ZIP members before creating any directories."""
    name_path = PurePosixPath(name)
    if (not name or '\\' in name or ':' in name or name_path.is_absolute()
            or '..' in name_path.parts or name_path.as_posix() != name
            or name in ('.', 'manifest.json')):
        raise CatalogueIntegrityError('Unsafe snapshot member: '+name)
    path = root.joinpath(*name_path.parts).resolve()
    if not path.is_relative_to(root):
        raise CatalogueIntegrityError('Snapshot member escaped release directory')
    return path


class RegionalSnapshot(SourcePilot):
    """Hash bytes while copying once; never read the acquisition folder again."""

    def __init__(self, source, *, directory=None, max_readers=48):
        self._readers = RasterReaders(max_readers)
        self._storage = tempfile.TemporaryDirectory(prefix='quiet-uk-serving-', dir=directory)
        self._fingerprints = {}
        self._bundle_lock = threading.Lock()
        self._bundle_fingerprint = None
        self._closed = False
        root = Path(self._storage.name).resolve()/'release'
        root.mkdir()
        try:
            manifest = copy.deepcopy(source.manifest)
            for name, expected in manifest['files'].items():
                original = member_path(source.root, name)
                target = member_path(root, name)
                target.parent.mkdir(parents=True, exist_ok=True)
                digest = hashlib.sha256()
                # Hash the exact bytes copied, not a path opened a second time.
                with original.open('rb') as incoming, target.open('xb') as outgoing:
                    while chunk := incoming.read(COPY_CHUNK):
                        digest.update(chunk)
                        outgoing.write(chunk)
                if digest.hexdigest() != expected:
                    raise CatalogueIntegrityError('Snapshot checksum mismatch: '+name)
                target.chmod(stat.S_IREAD)
                self._fingerprints[name] = fingerprint(target)
            (root/'manifest.json').write_bytes(json_bytes(manifest))
            # Retain manifest, lineage and evidence-contract validation. The
            # overridden verified_path uses only the copies just hashed above.
            super().__init__(root)
        except BaseException:
            self.close()
            raise

    def verified_path(self, name):
        if self._closed or name not in self.manifest['files']:
            raise CatalogueIntegrityError('Unknown or closed snapshot member')
        path = member_path(self.root, name)
        if fingerprint(path) != self._fingerprints[name]:
            raise CatalogueIntegrityError('Private snapshot file changed: '+name)
        return path

    @contextmanager
    def open_raster(self, name):
        with self._readers.open(self.verified_path(name)) as reader:
            yield reader
            self.verified_path(name)

    def bundle_path(self):
        """Build once on disk; concurrent requests reuse the completed archive."""
        with self._bundle_lock:
            if self._closed:
                raise CatalogueIntegrityError('Snapshot is closed')
            path = Path(self._storage.name)/'evidence.zip'
            if self._bundle_fingerprint is None:
                partial = path.with_suffix('.partial')
                try:
                    self.write_bundle(partial)
                    partial.replace(path)
                    path.chmod(stat.S_IREAD)
                    self._bundle_fingerprint = fingerprint(path)
                finally:
                    partial.unlink(missing_ok=True)
            if fingerprint(path) != self._bundle_fingerprint:
                raise CatalogueIntegrityError('Private evidence archive changed')
            return path

    def close(self):
        self._closed = True
        self._readers.close()
        self._storage.cleanup()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()
