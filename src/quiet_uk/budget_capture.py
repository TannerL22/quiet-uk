"""Serial, journalled HTTP capture with cumulative budgets across restarts."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil
import time

import requests

from .explorer import file_hash, json_bytes


class AcquisitionStopped(RuntimeError):
    pass


def atomic_json(path, value):
    pending = path.with_suffix(path.suffix+'.pending')
    pending.write_bytes(json_bytes(value))
    pending.replace(path)


class BudgetCapture:
    """A started but unfinished attempt reserves its full response allowance.

    Callers must hold a ResourceLock for root. Failures are retained, never
    overwritten. Repeating the same command can retry a failed request.
    """
    def __init__(self, root, limits, *, get=None, sleep=time.sleep):
        self.root = Path(root)
        self.limits = limits
        self.get = get or requests.get
        self.sleep = sleep
        self.last = time.monotonic()
        (self.root/'attempts').mkdir(parents=True, exist_ok=True)

    def usage(self):
        records = [json.loads(p.read_text('utf-8')) for p in (self.root/'attempts').glob('*/*.json')]
        return {'http_attempts': len(records), 'charged_bytes': sum(
            r['bytes'] if r['state'] == 'finished' else self.limits['response_bytes'] for r in records)}

    def fetch(self, url, params=None, *, required=True):
        prepared = requests.Request('GET', url, params=params).prepare().url
        key = hashlib.sha256(prepared.encode()).hexdigest()
        folder = self.root/'attempts'/key
        folder.mkdir(exist_ok=True)
        attempts = sorted(folder.glob('*.json'))
        for record_path in attempts:
            record = json.loads(record_path.read_text('utf-8'))
            if record['request_url'] != prepared:
                raise AcquisitionStopped('Request identity mismatch')
            if record['state'] == 'finished':
                body = record_path.with_suffix('.bin')
                if file_hash(body) != record['sha256'] or body.stat().st_size != record['bytes']:
                    raise AcquisitionStopped('Captured response changed')
                if record.get('complete') and not record.get('rejected') and (record['status'] == 200 or not required):
                    return body, record_path
        if len(attempts) >= self.limits['attempts_per_request']:
            raise AcquisitionStopped('Per-request attempt budget exhausted')
        usage = self.usage()
        if (usage['http_attempts'] >= self.limits['http_attempts']
                or usage['charged_bytes']+self.limits['response_bytes'] > self.limits['transfer_bytes']):
            raise AcquisitionStopped('Cumulative HTTP/transfer budget exhausted')
        if shutil.disk_usage(self.root).free < self.limits['min_free_disk_bytes']:
            raise AcquisitionStopped('Free disk reserve reached')
        self.sleep(max(0, self.limits['interval_seconds']-(time.monotonic()-self.last)))
        record_path = folder/f'{len(attempts)+1:03}.json'
        body = record_path.with_suffix('.bin')
        record = {'request_url': prepared, 'state': 'started',
                  'started_at_utc': datetime.now(timezone.utc).isoformat()}
        atomic_json(record_path, record)  # reserve before any network activity
        received, digest, response = 0, hashlib.sha256(), None
        try:
            self.last = time.monotonic()
            # Redirects are retained as responses, not hidden extra HTTP calls.
            response = self.get(url, params=params, stream=True, allow_redirects=False,
                                timeout=(15, 90), headers={'User-Agent': 'QuietUK-TiledCanary/1.0'})
            record.update(status=response.status_code, response_url=response.url,
                          response_headers={k: v for k, v in response.headers.items()
                                            if k.lower() in ('content-type', 'content-encoding', 'etag', 'last-modified', 'date', 'location')})
            with body.open('xb') as outgoing:
                for chunk in response.iter_content(chunk_size=64*1024):
                    if received+len(chunk) > self.limits['response_bytes']:
                        raise AcquisitionStopped('Response size budget exceeded')
                    outgoing.write(chunk); digest.update(chunk); received += len(chunk)
            record['complete'] = True
        except (requests.RequestException, OSError, AcquisitionStopped) as exc:
            record['complete'] = False
            record['failure'] = type(exc).__name__
            raise AcquisitionStopped(f'Capture interrupted; evidence retained ({type(exc).__name__})') from exc
        finally:
            if response is not None:
                response.close()
            # An interrupted process before here leaves the full reservation.
            if not body.exists():
                body.touch()
            record.update(state='finished', bytes=received, sha256=digest.hexdigest())
            atomic_json(record_path, record)
        if required and response.status_code != 200:
            raise AcquisitionStopped(f'HTTP {response.status_code}; response retained for audit')
        return body, record_path

    def __call__(self, root, name, url, params=None, required=True):
        """Adapter for the existing small metadata inventory, not large TIFFs."""
        path = self.root/name
        sidecar = self.root/(name+'.http.json')
        prepared = requests.Request('GET', url, params=params).prepare().url
        if sidecar.exists():
            record = json.loads(sidecar.read_text('utf-8'))
            if (record['request_url'] != prepared or file_hash(path) != record['sha256']
                    or not record.get('complete') or (required and record['status'] != 200)):
                raise AcquisitionStopped('Metadata capture changed')
            return path
        body, journal = self.fetch(url, params, required=required)
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(body, path)
        atomic_json(sidecar, json.loads(journal.read_text('utf-8')))
        return path
