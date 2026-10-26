import math

import pytest

from quiet_uk.propagation_canary import flat_boundary
from quiet_uk.terrain_boundary import (
    ground_average, path_difference, pure_horizontal_diffraction, rayleigh_selection,
    single_edge_boundary,
)


def scene(points=((0, 0), (100, 0)), source=(0, 1), receiver=(100, 4), edge=(50, 6),
          frequency=1000, g=.7, **kwargs):
    config = dict(edge=edge, scene_kind='synthetic', edge_width_m=0., opaque=True, infinite_lateral_extent=True)
    config.update(kwargs)
    return single_edge_boundary(points, source, receiver, frequency, [(points[0][0], points[-1][0], g)], 0., **config)


def test_annex_single_edge_uses_constant_ch_even_when_edge_above_side_plane_is_zero():
    # The 2012 low-edge multiplier would incorrectly zero this terrain ridge.
    r = scene(points=[(0, 0), (50, 10), (100, 0)], edge=(50, 10), frequency=63)
    assert r['branch']=='diffraction_including_ground'
    assert r['source_frame']['receiver']['equivalent_height_m']==pytest.approx(0, abs=1e-12)
    assert r['main_diffraction_uncapped_db']>4.77
    assert r['boundary_db'] is not None


@pytest.mark.parametrize('frequency', [63, 125, 250, 500, 1000, 2000, 4000, 8000])
@pytest.mark.parametrize('g', [0, .3, .7, 1])
def test_high_flat_screen_agrees_with_previous_mathematically_shared_subset(frequency, g):
    old = flat_boundary(frequency, 100, 1, 4, g, screen=dict(x_m=50, height_m=6))
    new = scene(frequency=frequency, g=g)
    assert new['boundary_db']==pytest.approx(old['boundary_db'], abs=1e-10)
    assert new['separate_ground_db']==0
    assert new['annual_noise_prediction'] is None and not new['regulatory_implementation_qualified']


def test_rayleigh_requires_both_strict_conditions_for_clear_paths():
    f = 1000; wavelength = 340/f
    assert not rayleigh_selection(f, -wavelength/20, 2)['use_diffraction']
    assert not rayleigh_selection(f, 0, wavelength/4)['use_diffraction']
    assert rayleigh_selection(f, 0, wavelength/4+1e-8)['use_diffraction']
    assert not rayleigh_selection(f, -.1, 100)['use_diffraction']
    assert rayleigh_selection(f, -.1, -100, blocked=True)['use_diffraction']


def test_signed_delta_uses_each_mirror_pairs_own_direct_distance():
    assert path_difference([0, 1], [100, 1], [50, 4])['delta_m']>0
    assert path_difference([0, 4], [100, 4], [50, 1])['delta_m']<0
    r = path_difference([0, 1], [100, 1], [50, 1])
    assert r['delta_m']==0 and r['tangent'] and not r['blocked']
    assert pure_horizontal_diffraction(1000, 0)==pytest.approx(10*math.log10(3))
    assert pure_horizontal_diffraction(1000, -.02)==0


def test_ground_average_weights_lengths_and_preserves_any_unknown_interval():
    assert ground_average([(0, 20, 0), (20, 100, 1)], 0, 100)==pytest.approx(.8)
    assert ground_average([(0, 20, 0), (20, 100, None)], 0, 100) is None
    with pytest.raises(ValueError): ground_average([(0, 20, 0), (21, 100, 1)], 0, 100)
    with pytest.raises(ValueError): ground_average([(0, 99, 0)], 0, 100)
    with pytest.raises(ValueError): ground_average([(0, 100, 1.1)], 0, 100)


def test_only_main_diffraction_is_capped_and_ground_is_not_added_twice():
    r = scene(edge=(50, 100), frequency=8000)
    assert r['main_diffraction_uncapped_db']>25
    assert r['main_diffraction_db']==25
    assert r['source_image_diffraction_db']>25
    assert r['boundary_db']==pytest.approx(25+r['source_ground_coupling_db']+r['receiver_ground_coupling_db'])


@pytest.mark.parametrize('end', ['source', 'receiver', 'both'])
def test_below_mean_plane_special_rule_retains_original_positions(end):
    points = [(0, 0), (10, 10), (190, 10), (200, 0)]
    source, receiver = (0, 1), (200, 1)
    if end=='source':
        points = [(0, 0), (10, 10), (200, 10)]; receiver=(200, 14); edge=(170, 400)
    elif end=='receiver':
        points = [(0, 10), (190, 10), (200, 0)]; source=(0, 14); edge=(30, 400)
    else:
        edge = (100, 400)
    r = scene(points=points, source=source, receiver=receiver, edge=edge, g=0)
    assert r['status']=='conditional_research'
    for name in ('source', 'receiver'):
        frame = r[name+'_frame'][name]
        if end in (name, 'both'):
            assert frame['signed_height_m']<0 and r[name+'_below_mean_plane_rule']
            assert r[name+'_image_diffraction_db']==r['main_diffraction_uncapped_db']
            assert r[name+'_ground_coupling_db']==r[name+'_ground_db']
        assert frame['original']==list(source if name=='source' else receiver)


def test_vertical_datum_translation_does_not_change_boundary():
    points = [(0, 20), (50, 35), (100, 40)]
    baseline = scene(points=points, source=(0, 21), receiver=(100, 44), edge=(50, 35))
    shifted = scene(points=[(x, z+10000) for x, z in points], source=(0, 10021), receiver=(100, 10044), edge=(50, 10035))
    assert shifted['boundary_db']==pytest.approx(baseline['boundary_db'], abs=1e-10)
    assert shifted['source_mirror'][1]-10000==pytest.approx(baseline['source_mirror'][1])


@pytest.mark.parametrize('kwargs', [dict(conditions='favourable'), dict(scene_kind='real_area'),
    dict(edge_width_m=None), dict(opaque=None), dict(infinite_lateral_extent=None),
    dict(edge_width_m=2), dict(opaque=False), dict(infinite_lateral_extent=False)])
def test_unsupported_or_unknown_scene_properties_remain_withheld(kwargs):
    r = scene(**kwargs)
    assert r['status']=='withheld' and r['boundary_db'] is None and r['annual_noise_prediction'] is None


def test_missing_inputs_and_multiple_visible_edges_do_not_become_noise_results():
    cases = [scene(g=None), scene(source=None), scene(points=[(0, 0), (50, None), (100, 0)]),
        scene(edge=(50, None)), scene(frequency=100),
        scene(points=[(0, 0), (25, 10), (50, 1), (75, 10), (100, 0)], edge=(25, 10)),
        scene(points=[(0, 0), (50, -10), (100, 0)], edge=None)]
    assert all(r['status']=='withheld' and r['boundary_db'] is None for r in cases)


def test_other_clear_terrain_corner_requires_selection_instead_of_using_wrong_edge():
    r = scene(points=[(0, 0), (20, 3), (50, 0), (100, 0)], source=(0, 10), receiver=(100, 10), edge=(50, 1))
    assert r['status']=='withheld' and r['reason']=='another_edge_requires_selection'


@pytest.mark.parametrize('kwargs', [dict(edge=(0, 6)), dict(edge=(100, 6)), dict(edge=(50, -1)),
    dict(source=(0, -1)), dict(frequency=math.nan), dict(opaque='yes')])
def test_invalid_physical_or_numeric_inputs_are_refused(kwargs):
    with pytest.raises(ValueError): scene(**kwargs)
