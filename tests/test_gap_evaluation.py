import numpy as np
import pytest

from quiet_uk.gap_evaluation import component_inventory, predict_patch, evaluate_core


def test_component_inventory_excludes_core_border_and_keeps_diagonal_connection():
    values=np.full((10,10),42.); reported=np.ones(values.shape,dtype=bool)
    reported[3,3]=reported[4,4]=False
    reported[0,7]=reported[1,7]=False
    result=component_inventory(values,reported,40)
    assert result['enclosed_components']==1 and result['enclosed_unknown_cells']==2
    assert result['border_components_excluded']==1 and result['unknown_cells']==4
    assert result['buckets']==[{'size_cells':'1-4','ring':'lowest_5db_band','components':1}]
    assert not any('prediction' in key for key in result)


def test_interpolators_never_use_hidden_truth_and_obey_perimeter_extrema():
    y,x=np.mgrid[:11,:11]; values=40+x*.8+y*.5
    before=predict_patch(values)
    edited=values.copy(); edited[1:-1,1:-1]=np.nan
    after=predict_patch(edited)
    for method,prediction in before.items():
        np.testing.assert_allclose(prediction,after[method])
        assert prediction.min()>=40-1e-9 and prediction.max()<=53+1e-9
    np.testing.assert_allclose(before['harmonic_db'],values[1:-1,1:-1],atol=1e-10)


def test_missing_perimeter_cannot_be_interpolated():
    values=np.full((5,5),42.); values[0,2]=np.nan
    with pytest.raises(ValueError,match='fully reported'): predict_patch(values)


def test_whole_patch_holdout_finds_an_interior_hotspot_in_low_ring_stratum():
    values=np.full((5,5),42.); values[2,2]=80
    results=evaluate_core(values,np.ones(values.shape,dtype=bool),40,42,per_stratum=1,sizes=(3,))
    assert len(results)==3 and {r['stratum'] for r in results}=={'near_reporting_floor'}
    for result in results:
        assert result['max_abs_error_db']==pytest.approx(38)
        assert result['truth_max_db']==80


def test_seeded_patch_selection_is_repeatable_and_full_unknown_patch_is_excluded():
    values=np.full((40,40),42.); reported=np.ones(values.shape,dtype=bool); reported[:20,:20]=False
    a=evaluate_core(values,reported,40,123,per_stratum=3,sizes=(3,9))
    assert a==evaluate_core(values,reported,40,123,per_stratum=3,sizes=(3,9))
    for result in a:
        r,c,n=result['row'],result['column'],result['patch_size_cells']; radius=(n+2)//2
        assert reported[r-radius:r+radius+1,c-radius:c+radius+1].all()
        assert result['mae_db']<1e-10
