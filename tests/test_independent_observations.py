import hashlib
import importlib.util
import json
from pathlib import Path
import threading

import pytest
import requests

from quiet_uk.catalogue import CatalogueIntegrityError
from quiet_uk.explorer import json_bytes
from quiet_uk.explorer_server import ExplorerServer
from quiet_uk.independent_observations import IndependentObservations, qualify_rows

ROOT = Path(__file__).parents[1]


def survey(group='one', level='52.5', duration='30', **values):
    return dict(LocationID='park', SessionID='session', GroupID=group, RecordingLength=duration,
                **{'LAeq_L(A)':level}, start_time='2019-05-01 10:00', **values)


LOCATIONS = {'park': {'name':'Park', 'latitude':51.5, 'longitude':-.1, 'position_support':'Approximate'}}


def release(tmp_path):
    observations, geojson, qa = qualify_rows([survey(), survey()], LOCATIONS)
    manifest = {'schema_version':1, 'qa':qa, 'files':{}}
    for name, value in (('observations.json',observations),('locations.geojson',geojson)):
        data=json_bytes(value); (tmp_path/name).write_bytes(data)
        manifest['files'][name]=hashlib.sha256(data).hexdigest()
    manifest['release_id']='observations-'+hashlib.sha256(json_bytes(manifest)).hexdigest()[:20]
    (tmp_path/'manifest.json').write_bytes(json_bytes(manifest))
    return manifest


def test_shared_survey_rows_are_one_recording_and_preserve_dates():
    a=survey(); b=survey(); b['start_time']='2019-05-02 10:00'; b['latitude']='51.6'
    rows, geojson, qa=qualify_rows([a,b], LOCATIONS)
    assert len(rows)==1 and rows[0]['source_rows']==[2,3]
    assert rows[0]['survey_dates']==['2019-05-01','2019-05-02']
    assert geojson['features'][0]['geometry']['coordinates']==[-.1,51.5]
    assert qa['selected_survey_rows']==2
    assert rows[0]['uncertainty_db'] is None


@pytest.mark.parametrize('field,value', [('LAeq_L(A)','53'),('RecordingLength','31'),('SessionID','another'),('LocationID','other')])
def test_conflicting_recording_is_withheld(field,value):
    b=survey(); b[field]=value
    locations=dict(LOCATIONS,other=LOCATIONS['park'])
    rows, _, qa=qualify_rows([survey(),b], locations)
    assert rows==[] and len(qa['excluded_groups'])==1


@pytest.mark.parametrize('level,duration', [('NaN','30'),('Inf','30'),('','30'),('52','0'),('52','601'),('52','NaN')])
def test_invalid_acoustic_value_is_withheld(level,duration):
    rows, _, qa=qualify_rows([survey(level=level,duration=duration)],LOCATIONS)
    assert rows==[] and len(qa['excluded_groups'])==1


def test_missing_recording_identity_is_not_a_measurement():
    rows,_,_=qualify_rows([survey(group='UNKNOWN')], LOCATIONS)
    assert not rows


def test_snapshot_pins_verified_bytes(tmp_path):
    manifest=release(tmp_path); snapshot=IndependentObservations(tmp_path)
    expected=snapshot.download()
    (tmp_path/'observations.json').write_text('[]')
    assert snapshot.download()==expected
    with pytest.raises(CatalogueIntegrityError,match='content mismatch'):
        IndependentObservations(tmp_path)
    assert snapshot.context()['manifest']['release_id']==manifest['release_id']


def test_manifest_edits_are_rejected(tmp_path):
    release(tmp_path)
    path=tmp_path/'manifest.json'; data=json.loads(path.read_bytes()); data['qa']['accepted_recordings']=7
    path.write_bytes(json_bytes(data))
    with pytest.raises(CatalogueIntegrityError,match='identity mismatch'):
        IndependentObservations(tmp_path)


def test_observations_http_contract_and_query_rejection(tmp_path):
    manifest=release(tmp_path)
    server=ExplorerServer(0,None,ROOT/'explorer',observations=tmp_path)
    worker=threading.Thread(target=server.serve_forever); worker.start()
    base=f'http://127.0.0.1:{server.server_port}'
    try:
        app=requests.get(base+'/api/app').json()
        assert app['interface_version']==6 and app['observations_release']==manifest['release_id']
        data=requests.get(base+'/api/observations').json()
        assert data['locations']['features'][0]['properties']['sample_count']==1
        assert 'observations' not in data
        response=requests.get(base+'/downloads/observations.json')
        assert len(response.json()['observations'])==1
        assert response.headers['Content-Disposition'].endswith(manifest['release_id']+'.json"')
        assert requests.get(base+'/api/observations?path=secret').status_code==400
        assert requests.get(base+'/api/observations',headers={'Origin':'https://example.org'}).status_code==403
    finally:
        server.shutdown(); worker.join(); server.server_close()


def test_acquisition_budget_checksum_resume_and_host_allowlist(tmp_path):
    spec=importlib.util.spec_from_file_location('acquire',ROOT/'scripts/37_acquire_independent_evidence.py')
    module=importlib.util.module_from_spec(spec); spec.loader.exec_module(module)
    class Response:
        status_code=200; url='https://zenodo.org/test'; headers={}
        def __enter__(self): return self
        def __exit__(self,*args): pass
        def raise_for_status(self): pass
        def iter_content(self,size): yield b'abc'; yield b'def'
    class Session:
        def get(self,*args,**kwargs): return Response()
    with pytest.raises(ValueError,match='byte budget'):
        module.capture(Session(),Response.url,tmp_path/'large',5)
    assert (tmp_path/'large.part').read_bytes()==b'abc'
    with pytest.raises(ValueError,match='Only HTTPS'):
        module.capture(Session(),'http://zenodo.org/test',tmp_path/'bad',6)
    path=tmp_path/'good'; checksum='md5:'+hashlib.md5(b'abcdef').hexdigest()
    first=module.capture(Session(),Response.url,path,6,checksum)
    assert module.capture(None,Response.url,path,6,checksum)==first
    path.write_bytes(b'edited')
    with pytest.raises(ValueError,match='changed'):
        module.capture(None,Response.url,path,6,checksum)
