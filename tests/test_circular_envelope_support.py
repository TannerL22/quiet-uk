"""A steep unused roof/terrain corner must not invalidate an enclosing ray."""
import importlib.util
from pathlib import Path
import random
import pytest
from quiet_uk.favourable_propagation import circular_envelope

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('independent_circular_reference',ROOT/'scripts/99_verify_curved_envelope_fix.py')
reference=importlib.util.module_from_spec(spec);spec.loader.exec_module(reference)

def compare(points,s=(0.,1.),r=(100.,4.),radius=1000.):
    expected=reference.independent_envelope(points,s,r,radius)['path']
    assert circular_envelope(points,s,r,radius)==expected
    return expected

def test_minimal_reduction_of_preserved_near_roof_failure():
    s=(0.,29.210999298095704);r=(445.796168353934,33.40950012207031);radius=3566.527508965261
    points=[(41.553246300583645,41.687503814697266),(51.46573233143527,29.53849983215332),(51.46956451758743,41.687503814697266)]
    assert compare(points,s,r,radius)==[list(s),list(points[0]),list(r)]

def test_steep_unused_falling_corner_after_a_roof():
    path=compare([(20.,12.),(40.,12.),(40.00001,0.),(60.,0.)])
    assert path[1:-1]==[[20.,12.],[40.,12.]]

def test_deep_unused_valley_does_not_require_a_local_minor_arc():
    assert compare([(25.,0.),(25.00000001,-100.),(26.,0.),(75.,0.)])==[[0.,1.],[100.,4.]]

@pytest.mark.parametrize('seed',range(32))
def test_matches_full_exhaustive_visibility_search_with_nearby_corners(seed):
    rng=random.Random(seed);xs=sorted(rng.uniform(5,95) for _ in range(5))
    points=[(x,rng.uniform(-5,20)) for x in xs]
    points.append((xs[2]+1e-7,rng.uniform(-10,25)));points.sort()
    compare(points)

@pytest.mark.parametrize('points,s,r,radius',[
    ([(500.,20000.)],(0.,1.),(1000.,4.),8000.),
    ([(.000001,10.)],(0.,1.),(100.,4.),1000.),
])
def test_genuinely_unsupported_selected_arcs_remain_unsupported(points,s,r,radius):
    with pytest.raises(ValueError):reference.independent_envelope(points,s,r,radius)
    with pytest.raises(ValueError):circular_envelope(points,s,r,radius)

@pytest.mark.parametrize('points,s,r,radius',[
    ([(50.,0.),(40.,0.)],(0.,1.),(100.,4.),1000.),
    ([(50.,0.),(50.,2.)],(0.,1.),(100.,4.),1000.),
    ([(0.,2.)],(0.,1.),(100.,4.),1000.),
    ([(100.,2.)],(0.,1.),(100.,4.),1000.),
    ([],(100.,1.),(0.,4.),1000.),
    ([],(0.,1.),(100.,4.),-1000.),
    ([(50.,float('nan'))],(0.,1.),(100.,4.),1000.),
])
def test_malformed_support_is_not_silently_pruned(points,s,r,radius):
    with pytest.raises(ValueError):circular_envelope(points,s,r,radius)
