"""Small cooperative operating-system locks for Quiet UK batch resources."""
from __future__ import annotations

import hashlib
import os
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path

if os.name == "nt":
    import msvcrt
else:
    import fcntl


class ResourceLockError(RuntimeError):
    """Raised when a batch resource is already held."""


_REGISTRY_LOCK = threading.RLock()
_HELD_KEYS: set[str] = set()


def canonical_resource_path(path: str | os.PathLike) -> str:
    """Return an absolute, resolved, platform-normalised resource path."""
    resolved = Path(path).expanduser().resolve(strict=False)
    return os.path.normcase(str(resolved))


def lock_directory() -> Path:
    """Return the stable per-user temporary directory for coordination files."""
    base = Path(tempfile.gettempdir())
    if os.name == "nt":
        # Windows' temporary directory is normally already user-scoped.
        return base / "quiet-uk-locks"
    user_key = str(getattr(os, "getuid", lambda: "unknown")())
    return base / f"quiet-uk-locks-{user_key}"


def lock_file_path(path: str | os.PathLike) -> Path:
    canonical = canonical_resource_path(path)
    digest = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return lock_directory() / f"{digest}.lock"


class ResourceLock:
    """A nonblocking OS lock held for the lifetime of this object."""

    def __init__(self, path: str | os.PathLike, resource_name: str = "resource"):
        self.canonical_path = canonical_resource_path(path)
        self.resource_name = resource_name
        self.key = self.canonical_path
        self.path = lock_file_path(self.canonical_path)
        self._handle = None
        self._registered = False

    def acquire(self) -> "ResourceLock":
        with _REGISTRY_LOCK:
            if self.key in _HELD_KEYS:
                raise ResourceLockError(
                    f"Quiet UK {self.resource_name} is already locked: {self.canonical_path}"
                )
            _HELD_KEYS.add(self.key)
            self._registered = True

        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._handle = self.path.open("a+b")
            if os.name == "nt":
                self._handle.seek(0, os.SEEK_END)
                if self._handle.tell() == 0:
                    self._handle.write(b"0")
                    self._handle.flush()
                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                fcntl.flock(self._handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            return self
        except (OSError, ValueError) as exc:
            self._close_after_failed_acquire()
            raise ResourceLockError(
                f"Quiet UK {self.resource_name} is already locked or unavailable: "
                f"{self.canonical_path}"
            ) from exc

    def _close_after_failed_acquire(self) -> None:
        if self._handle is not None:
            try:
                self._handle.close()
            except OSError:
                pass
            self._handle = None
        if self._registered:
            with _REGISTRY_LOCK:
                _HELD_KEYS.discard(self.key)
            self._registered = False

    def release(self) -> None:
        handle = self._handle
        self._handle = None
        try:
            if handle is not None:
                if os.name == "nt":
                    try:
                        handle.seek(0)
                        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
                    except OSError:
                        pass
                else:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
                    except OSError:
                        pass
                handle.close()
        finally:
            if self._registered:
                with _REGISTRY_LOCK:
                    _HELD_KEYS.discard(self.key)
                self._registered = False

    def __enter__(self) -> "ResourceLock":
        return self.acquire()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.release()


def resource_lock(path: str | os.PathLike, resource_name: str = "resource") -> ResourceLock:
    return ResourceLock(path, resource_name)


@contextmanager
def acquire_resource_locks(resources):
    """Acquire path locks in deterministic order, deduplicating equal paths."""
    unique = {}
    for resource_name, path in resources:
        canonical = canonical_resource_path(path)
        unique.setdefault(canonical, resource_name)
    locks = [
        ResourceLock(path, unique[path])
        for path in sorted(unique)
    ]
    acquired = []
    try:
        for lock in locks:
            acquired.append(lock.acquire())
        yield tuple(acquired)
    finally:
        for lock in reversed(acquired):
            lock.release()
