from types import SimpleNamespace

import numpy as np
import pytest
import rasterio
from rasterio.io import MemoryFile
from rasterio.warp import transform

from quiet_uk.catalogue import CatalogueIntegrityError
from quiet_uk.explorer import rgba_png
from quiet_uk.regional_display import RegionalDisplay, MAX_CACHE_BYTES, MAX_CACHE_ENTRIES
from quiet_uk.serving_snapshot import RegionalSnapshot
from test_source_pilot import pilot


def pixels(payload):
    with pytest.warns(rasterio.errors.NotGeoreferencedWarning):
        with MemoryFile(payload) as file:
            with file.open() as image:
                return image.read().transpose(1,2,0)


def test_overview_joins_display_pixels_without_blending_or_filling_unknowns(tmp_path):
    records = {}
    arrays = [np.array([[[192,184,165,130],[170,181,196,210]],[[0,0,0,0],[37,139,146,230]]],dtype='uint8'),
              np.array([[[171,96,93,230],[0,0,0,0]],[[170,181,196,130],[192,184,165,210]]],dtype='uint8')]
    for i, array in enumerate(arrays):
        name = f'{i}.png'; (tmp_path/name).write_bytes(rgba_png(array))
        lon, lat = transform('EPSG:3857','EPSG:4326',[i*20,(i+1)*20,(i+1)*20,i*20],[20,20,0,0])
        records[str(i)] = {'id':str(i),'site':'area','source':'road','metric':'Lnight',
                           'display':{'path':name,'corners':list(map(list,zip(lon,lat)))}}
    snapshot = SimpleNamespace(records=records,manifest={'release_id':'pilot-'+'a'*20},verified_path=lambda name:tmp_path/name)
    display = RegionalDisplay(snapshot)
    group = display.groups['area/road/Lnight']
    assert (group['width'],group['height']) == (4,2)
    assert np.array_equal(pixels(display.overview('area/road/Lnight')),np.concatenate(arrays,axis=1))
    assert display.metadata()['analytical_use'] is False


def test_display_metadata_overview_and_cache_are_bounded(pilot):
    with RegionalSnapshot(pilot) as snapshot:
        display = RegionalDisplay(snapshot)
        assert display.metadata()['release_id'] == pilot.manifest['release_id']
        for key, group in display.groups.items():
            payload = display.overview(key)
            assert payload.startswith(b'\x89PNG')
            assert pixels(payload).shape == (group['height'],group['width'],4)
            assert max(group['width'],group['height']) <= 1024
        assert len(display.cache) <= MAX_CACHE_ENTRIES
        assert display.cache_bytes <= MAX_CACHE_BYTES
        with pytest.raises(KeyError): display.overview('unknown/road/Lden')


def test_cached_overview_still_checks_private_source_fingerprints(pilot):
    with RegionalSnapshot(pilot) as snapshot:
        display = RegionalDisplay(snapshot)
        key = 'heathrow/road/Lden'
        display.overview(key)
        record = snapshot.records[display.groups[key]['record_ids'][0]]
        path = snapshot.root/record['display']['path']
        path.chmod(0o600); path.write_bytes(b'changed')
        with pytest.raises(CatalogueIntegrityError,match='changed'):
            display.overview(key)
