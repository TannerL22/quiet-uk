"""Offline map derivative of a sealed canary; source evidence stays byte-exact."""
import copy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import shutil

from rasterio.warp import transform

from . import source_pilot as p
from . import tiled_canary as c
from .budget_capture import atomic_json
from .catalogue import CatalogueIntegrityError
from .evidence_semantics import CONTRACT, record_evidence
from .locking import ResourceLock


def prefixed(value, prefix):
    """Remap only file references, retaining coverage IDs, URLs and hashes."""
    if isinstance(value, dict):
        return {key: prefix+item if key in ('path', 'http_record') or key.endswith('_file')
                else prefixed(item, prefix) for key, item in value.items()}
    if isinstance(value, list):
        return [prefixed(item, prefix) for item in value]
    return value


def footprint(bounds):
    w, s, e, n = bounds
    lon, lat = transform('EPSG:27700', 'EPSG:4326', [w,e,e,w,w], [n,n,s,s,n])
    return {'type': 'Polygon', 'coordinates': [list(map(list, zip(lon, lat)))]}


def derived_records(recipe, acquired, regional):
    records = []
    for original in acquired:
        if original['status'] != 'accepted':
            raise ValueError('Map release requires every requested layer; no silent provider gaps')
        tile = next(t for t in recipe['tiles'] if t['id'] == original['tile'])
        if c.intersection(tile['core_bounds'], original['request_bounds']) != tile['core_bounds']:
            raise ValueError('Map release requires complete native core coverage')
        record = prefixed(copy.deepcopy(original), 'canary/')
        record.update(id=f'canary-{original["tile"]}-{original["source"]}-{original["metric"]}',
                      site='canary', core_bounds=tile['core_bounds'])
        records.append(record)
    for original in regional['records']:
        if original['site'] == 'heathrow':
            record = prefixed({k: copy.deepcopy(v) for k,v in original.items() if k not in ('display','evidence','footprint')}, 'regional/')
            record['core_bounds'] = original['qa']['bounds']
            records.append(record)
    for record in records:
        record['footprint'] = footprint(record['core_bounds'])
    return records


def sites_for(recipe, regional):
    w,s,e,n = recipe['bounds']
    lon, lat = transform('EPSG:27700', 'EPSG:4326', [(w+e)/2], [(s+n)/2])
    sites = {'canary': {'name': 'Oxford–Reading–Chilterns', 'center': [lon[0],lat[0]],
                        'size_m': e-w, 'footprint': footprint(recipe['bounds'])}}
    if 'heathrow' in regional['sites']:
        sites['heathrow'] = copy.deepcopy(regional['sites']['heathrow'])
        record = next(r for r in regional['records'] if r['site'] == 'heathrow')
        sites['heathrow']['footprint'] = footprint(record['qa']['bounds'])
    return sites


def validate_lineage(release):
    """Reconstruct mapped records from pinned parents, not editable summaries."""
    m = release.manifest
    read = lambda name: json.loads(release.verified_path(name).read_text('utf-8'))
    canary, regional = read('canary/manifest.json'), read('regional/manifest.json')
    for prefix, parent, kind in [('canary/',canary,'canary-'), ('regional/',regional,'pilot-')]:
        base = {k:v for k,v in parent.items() if k != 'release_id'}
        if parent['release_id'] != kind+hashlib.sha256(p.json_bytes(base)).hexdigest()[:20]:
            raise CatalogueIntegrityError('Tiled parent identity changed')
        if any(m['files'].get(prefix+name) != digest for name,digest in parent['files'].items()):
            raise CatalogueIntegrityError('Tiled release changed parent bytes')
    recipe = read('canary/plan.json')
    products = prefixed(read('canary/products.json'), 'canary/')
    if (recipe != c.plan(recipe['bounds']) or not canary['verification']['complete']
            or m['parent_release_id'] != canary['release_id']
            or canary['reference_release'] != regional['release_id']
            or m['products'] != products or m['sites'] != sites_for(recipe, regional)):
        raise CatalogueIntegrityError('Tiled recipe, products or lineage changed')
    for source in p.PROVIDERS:
        for metric in p.METRICS:
            if c.product_identity(products[source], metric) != c.product_identity(regional['products'][source], metric):
                raise CatalogueIntegrityError('Regional product/period mismatch')
    expected = derived_records(recipe, read('canary/records.json'), regional)
    keys = {(t['id'],s,m) for t in recipe['tiles'] for s in p.PROVIDERS for m in p.METRICS}
    actual = [(r['tile'],r['source'],r['metric']) for r in expected if r['site'] == 'canary']
    if set(actual) != keys or len(actual) != len(keys) or len(expected) != len(m['records']):
        raise CatalogueIntegrityError('Incomplete or duplicate mapped cores')
    for original, record in zip(expected, m['records']):
        if ({k:v for k,v in record.items() if k not in ('display','evidence')} != original
                or m['files'].get(record['path']) != record['sha256']
                or record['evidence'] != record_evidence(record, products[record['source']], m['files'])):
            raise CatalogueIntegrityError('Tiled analytical record changed')
        if record['display']['path'] != f'display/{record["id"]}.png':
            raise CatalogueIntegrityError('Tiled display path changed')


def verify_release(release, *, reproduce=False):
    validate_lineage(release)
    reference = p.SourcePilot(release.root/'regional')
    report = c.verify(release.root/'canary', reference)
    reference.verify()
    if reproduce:
        for record in release.records.values():
            png, corners = p.display_image(release.verified_path(record['path']), evidence_version=CONTRACT['version'], core_bounds=record['core_bounds'])
            if png != release.verified_path(record['display']['path']).read_bytes() or corners != record['display']['corners']:
                raise CatalogueIntegrityError('Tiled display reproduction mismatch')
    return {'release_id': release.manifest['release_id'], 'verified_records': len(release.records),
            'verified_files': len(release.manifest['files']), 'display_reproduced': reproduce, 'extraction': report}


def publish(canary_root, regional_root, output):
    canary_root, regional_root, output = map(lambda v: Path(v).resolve(), (canary_root, regional_root, output))
    if any(output.is_relative_to(parent) or parent.is_relative_to(output) for parent in (canary_root, regional_root)):
        raise ValueError('Use a separate output directory')
    reference = p.SourcePilot(regional_root)
    if reference.manifest['schema_version'] != 2:
        raise ValueError('An evidence-semantics regional release is required')
    reference.verify()
    c.verify(canary_root, reference)
    canary = json.loads((canary_root/'manifest.json').read_text('utf-8'))
    with ResourceLock(output, 'tiled map construction'):
        if output.exists():
            raise FileExistsError('Use a new tiled map destination')
        output.mkdir(parents=True)
        for prefix, root, manifest in [('canary',canary_root,canary), ('regional',regional_root,reference.manifest)]:
            for name in [*manifest['files'], 'manifest.json']:
                target = output/prefix/name
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(root/name, target)
                expected = manifest['files'].get(name, p.file_hash(root/name))
                if p.file_hash(target) != expected:
                    raise CatalogueIntegrityError('Source changed while copying')
        recipe = json.loads((output/'canary/plan.json').read_text('utf-8'))
        records = derived_records(recipe, json.loads((output/'canary/records.json').read_text('utf-8')), reference.manifest)
        products = prefixed(json.loads((output/'canary/products.json').read_text('utf-8')), 'canary/')
        (output/'display').mkdir()
        files = {path.relative_to(output).as_posix(): p.file_hash(path) for path in output.rglob('*') if path.is_file()}
        for index, record in enumerate(records):
            record['evidence'] = record_evidence(record, products[record['source']], files)
            png, corners = p.display_image(output/record['path'], evidence_version=CONTRACT['version'], core_bounds=record['core_bounds'])
            name = f'display/{record["id"]}.png'
            (output/name).write_bytes(png)
            record['display'] = {'path':name, 'corners':corners, 'resampling':'nearest', 'analytical_use':False}
            print(f'{index+1}/{len(records)} displays built', flush=True)
        repo = Path(__file__).resolve().parents[2]
        for original in [*Path(__file__).parent.glob('*.py'), repo/'scripts/34_tiled_map.py', repo/'requirements-windows-py314-amd64.lock']:
            target = output/'construction'/original.relative_to(repo)
            target.parent.mkdir(parents=True, exist_ok=True); shutil.copyfile(original, target)
        manifest = {k:copy.deepcopy(reference.manifest[k]) for k in ('title','metrics','units','receiver_height_m','spatial_resolution_m','uncertainty','unavailable','licence','licence_url','attribution','display')}
        manifest.update(schema_version=3, created_at_utc=datetime.now(timezone.utc).isoformat(),
                        status='exploratory_tiled_source_release', parent_release_id=canary['release_id'],
                        evidence_contract=copy.deepcopy(CONTRACT), missing_value_policy=CONTRACT['missing_value_policy'],
                        products=products, sites=sites_for(recipe, reference.manifest), records=records,
                        display_recipe='core-only nearest, globally aligned EPSG:3857 10 m display pixels; native analytical cells unchanged',
                        files={path.relative_to(output).as_posix():p.file_hash(path) for path in sorted(output.rglob('*')) if path.is_file()})
        manifest['release_id'] = 'pilot-'+hashlib.sha256(p.json_bytes(manifest)).hexdigest()[:20]
        # Validate copied lineage before making a discoverable manifest.
        from types import SimpleNamespace
        validate_lineage(SimpleNamespace(manifest=manifest, verified_path=lambda name: output/name))
        atomic_json(output/'manifest.json', manifest)
    return manifest
