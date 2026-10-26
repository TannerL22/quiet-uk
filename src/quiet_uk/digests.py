"""File digests and canonical JSON bytes used by every generation inventory.

Formerly defined in quiet_uk.explorer (which re-exports them), so the road
engine no longer imports the explorer pipeline and its raster dependencies.
"""
import hashlib
import json
from pathlib import Path


def file_hash(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(value):
    return (json.dumps(value, ensure_ascii=False, allow_nan=False, indent=2) + '\n').encode('utf-8')
