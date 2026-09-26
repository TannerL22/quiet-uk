"""Core ownership, globally aligned display, portable lineage and HTTP serving."""
import copy
import io
import json
from pathlib import Path
import threading
from urllib.request import urlopen

import numpy as np
from PIL import Image
import pytest
import rasterio
from rasterio.transform import from_origin
from rasterio.warp import transform
from rasterio.io import MemoryFile
from rasterio.windows import from_bounds

from quiet_uk import source_pilot as p, tiled_canary as c, tiled_release as t
from quiet_uk.catalogue import CatalogueIntegrityError
from quiet_uk.explorer_server import ExplorerServer
from quiet_uk.comparison import compare_places, comparison_csv
from test_source_pilot import pilot
from test_tiled_canary import Response, limits


def test_display_four_cores_equal_single_canvas_without_gaps_or_double_opacity(tmp_path):
    path = tmp_path/'source.tif'
    w,s,e,n = 465005,185005,465405,185405
    data = np.zeros((40,40), dtype='float32')
    data[::3,:] = -96; data[1::3,::2] = 42; data[1::3,1::2] = 73
    with rasterio.open(path,'w',driver='GTiff',width=40,height=40,count=1,dtype='float32',nodata=-96,crs='EPSG:27700',transform=from_origin(w,n,10,10)) as ds:
        ds.write(data,1)
    def render(bounds):
        png, corners = p.display_image(path,evidence_version=1,core_bounds=bounds)
        x,y = transform('EPSG:4326','EPSG:3857',[corners[0][0]],[corners[0][1]])
        return np.array(Image.open(io.BytesIO(png))), (round(x[0]/10),round(-y[0]/10))
    whole, origin = render([w,s,e,n])
    mosaic = np.zeros_like(whole); owners = np.zeros(whole.shape[:2],dtype='uint8')
    for row in range(2):
        for col in range(2):
            tile, pos = render([w+col*200,s+row*200,w+(col+1)*200,s+(row+1)*200])
            dx,dy = pos[0]-origin[0],pos[1]-origin[1]
            window = np.s_[dy:dy+tile.shape[0],dx:dx+tile.shape[1]]
            present = tile[:,:,3] > 0
            owners[window] += present
            mosaic[window][present] = tile[present]
    assert owners.max() == 1
    assert np.array_equal(mosaic,whole)


@pytest.fixture
def tiled(pilot, tmp_path, monkeypatch):
    regional = tmp_path.with_name(tmp_path.name+'-regional')
    p.publish_interpretation(pilot.root, regional)
    reference = p.SourcePilot(regional)
    original = reference.manifest['records'][0]
    w,s,e,n = original['qa']['bounds']; s=n-200; e=w+200
    recipe = c.plan()
    recipe['bounds'] = [w,s,e,n]
    recipe['tiles'] = []
    for row in range(2):
        for col in range(2):
            core=[w+col*100,s+row*100,w+(col+1)*100,s+(row+1)*100]
            recipe['tiles'].append({'id':f'r{row}c{col}','row':row,'column':col,'core_bounds':core,
                                    'halo_bounds':c.intersection([core[0]-10,core[1]-10,core[2]+10,core[3]+10],recipe['bounds'])})
    recipe['limits'] = limits(response_bytes=1024**2,transfer_bytes=100*1024**2,http_attempts=100)
    monkeypatch.setattr(c,'plan',lambda *a,**k:copy.deepcopy(recipe))
    def inventory(destination, **kwargs):
        import shutil
        for name in [v['metadata_file'] for v in reference.manifest['products'].values()]:
            shutil.copyfile(reference.root/name,Path(destination)/name)
        return copy.deepcopy(reference.manifest['products'])
    monkeypatch.setattr(c.p,'inventory',inventory)
    monkeypatch.setattr(c,'description',lambda *a:{'status':'available','envelope':original['qa']['bounds'],'grid':original['native_grid']})
    def get(url, *, params, **kwargs):
        params=dict(params); source,metric=params['coverage'].split('-')
        record=next(r for r in reference.records.values() if r['source']==source and r['metric']==metric)
        with rasterio.open(reference.root/record['path']) as ds:
            win=from_bounds(*map(float,params['bbox'].split(',')),transform=ds.transform).round_offsets().round_lengths()
            data=ds.read(1,window=win)
            profile={**ds.profile,'width':data.shape[1],'height':data.shape[0],'transform':ds.window_transform(win)}
        with MemoryFile() as memory:
            with memory.open(**profile) as ds: ds.write(data,1)
            return Response(chunks=[memory.read()])
    monkeypatch.setattr(c.requests,'get',get)
    canary=tmp_path.with_name(tmp_path.name+'-canary')
    c.acquire(canary,reference)
    # Recovery can publish records in request-hash order, not tile/source order.
    import random, hashlib
    acquired=json.loads((canary/'records.json').read_text('utf-8'))
    random.Random(7).shuffle(acquired)
    (canary/'records.json').write_bytes(p.json_bytes(acquired))
    sealed=json.loads((canary/'manifest.json').read_text('utf-8'))
    sealed['files']['records.json']=p.file_hash(canary/'records.json')
    sealed.pop('release_id')
    sealed['release_id']='canary-'+hashlib.sha256(p.json_bytes(sealed)).hexdigest()[:20]
    (canary/'manifest.json').write_bytes(p.json_bytes(sealed))
    output=tmp_path.with_name(tmp_path.name+'-map')
    t.publish(canary,regional,output)
    monkeypatch.setattr(c.requests,'get',lambda *a,**k:pytest.fail('Offline operation made a provider request'))
    return p.SourcePilot(output),recipe,reference


def test_tiled_lookup_boundaries_replay_and_http(tiled,monkeypatch):
    release,recipe,reference=tiled
    assert release.verify(reproduce=True)['verified_records']==45
    w,s,e,n=recipe['bounds']; midx=(w+e)/2; midy=(s+n)/2
    # Pin exact projected coordinates to test ownership without CRS roundtrip error.
    real_transform=p.transform
    with monkeypatch.context() as patch:
        for x,y,tile in [(midx,midy,'r0c1'),(midx-.001,midy+.001,'r1c0'),(midx+.001,midy+.001,'r1c1'),(midx-.001,midy-.001,'r0c0'),(w,n,'r1c0'),(e,midy,None),(midx,s,None),(w-.001,midy,None)]:
            patch.setattr(p,'transform',lambda a,b,xs,ys:([x],[y]) if a=='EPSG:4326' else real_transform(a,b,xs,ys))
            result=release.lookup('canary',-1,52)
            assert len(result['observations'])==9
            assert {(o['source'],o['metric']) for o in result['observations']} == {(s,m) for s in p.PROVIDERS for m in p.METRICS}
            assert ('canary' in release.locate(-1,52)) == (tile is not None)
            assert all((f'canary-{tile}-' in o['record_id']) if tile else o['status']=='outside_extract' for o in result['observations'])
    # CRS path plus exact source values, encoding states and cell geometry.
    lon,lat=transform('EPSG:27700','EPSG:4326',[w+5],[n-5])
    result=release.lookup('canary',lon[0],lat[0])
    old=reference.lookup('heathrow',lon[0],lat[0])
    assert all(o['raw_value']==a['raw_value'] and o['status']==a['status'] and o['cell']==a['cell'] for o,a in zip(result['observations'],old['observations']))
    assert all(o['value_db'] is None and o['bounds'] is None for o in result['observations'])
    comparison=compare_places(release,[{'site':'canary','longitude':lon[0],'latitude':lat[0],'label':'Unknown cell'}],release.manifest['release_id'])
    assert comparison['schema_version']==2
    assert b'unreported_unknown' in comparison_csv(comparison)
    # The exported release replays without either original acquisition directory.
    import zipfile
    portable=release.root.with_name(release.root.name+'-portable')
    archive=release.root.with_suffix('.zip')
    release.write_bundle(archive)
    with zipfile.ZipFile(archive) as bundle: bundle.extractall(portable)
    assert p.SourcePilot(portable).verify(reproduce=True)['verified_records']==45
    server=ExplorerServer(0,None,Path(__file__).parents[1]/'explorer',pilot=release)
    worker=threading.Thread(target=server.serve_forever,daemon=True);worker.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        # Source edits after snapshot creation cannot change a running reader.
        record=release.records[result['observations'][0]['record_id']]
        (release.root/record['path']).write_bytes(b'changed externally')
        actual=json.load(urlopen(base+f'/api/pilot/location?site=canary&lon={lon[0]}&lat={lat[0]}'))
        assert actual==result
        png=urlopen(base+f'/pilot-images/{release.manifest["release_id"]}/{record["id"]}.png').read()
        assert png.startswith(b'\x89PNG')
        assert json.load(urlopen(base+f'/downloads/pilot-location.json?site=canary&lon={lon[0]}&lat={lat[0]}'))==result
    finally:
        server.shutdown();server.server_close();worker.join(5)
    assert not server.pilot.root.parent.exists()


def test_resealed_tile_ownership_tampering_is_rejected(tiled,monkeypatch):
    release,_,_=tiled
    from quiet_uk import display_seams
    pending=release.root.with_name(release.root.name+'-pending')
    with monkeypatch.context() as patch:
        def fail(*args): raise ValueError('Display seam rejected')
        patch.setattr(display_seams,'check',fail)
        with pytest.raises(ValueError,match='Display seam rejected'):
            t.publish(release.root/'canary',release.root/'regional',pending)
    assert not (pending/'manifest.json').exists()
    t.publish(release.root/'canary',release.root/'regional',pending,resume=True)
    assert p.SourcePilot(pending).verify(reproduce=True)['display_seams']['display_seam_pairs']==36
    m=release.manifest
    m['records'][0]['core_bounds'][2]+=10
    import hashlib
    m.pop('release_id')
    m['release_id']='pilot-'+hashlib.sha256(p.json_bytes(m)).hexdigest()[:20]
    (release.root/'manifest.json').write_bytes(p.json_bytes(m))
    with pytest.raises(CatalogueIntegrityError,match='analytical record'):
        p.SourcePilot(release.root)
