"""Declared facade hypotheses for an offline, incomplete single-reflection sum.

Absorption and incident-view eligibility below are hypotheses, not observations,
confidence limits or guaranteed noise bounds. The existing direct-scene policy
continues to govern the boundary calculation on each unfolded cross section.
"""
from dataclasses import dataclass

from .conditional_scene_domain import execution_policy, scene_supported
from .physical_scene_assumptions import assumption_cases

POLICY_ID = 'conditional-facade-reflections-2026-10-14-v1'
SCOPE = 'direct_plus_selected_single_reflections_incomplete'


def reflection_cases():
    return [dict(case_id=name, absorption_by_band=[alpha]*8,
                 slope_from_vertical_deg=0., incident_view_height_m=1.,
                 incident_view_width_m=1., wall_top='physical_case_flat_roof',
                 wall_base='native_terrain_at_plan_hit',
                 eligibility='assumed_1m_apparent_dimensions_in_both_conditions')
            for name, alpha in (('hard_facade', 0.), ('reference_facade', .2),
                                ('absorptive_facade', .5))]


@dataclass(frozen=True)
class ReflectionExecution:
    physical_case_id: str
    reflection_case_id: str
    reflection_policy_id: str = POLICY_ID
    path_scope: str = SCOPE
    real_area_validated: bool = False
    absolute_release_eligible: bool = False


def reflection_execution_policy(physical_case, reflection_case):
    execution_policy(physical_case)
    if not any(reflection_case == c for c in reflection_cases()):
        raise ValueError('Use an exact declared reflection case')
    return ReflectionExecution(physical_case['case_id'], reflection_case['case_id'])


def reflection_scene_supported(scene, wall, policy=None):
    if scene.get('scene_kind') in ('synthetic', 'published_reference'):
        return True
    if type(policy) is not ReflectionExecution or wall is None:
        return False
    physical = next((c for c in assumption_cases() if c['case_id'] == policy.physical_case_id), None)
    reflection = next((c for c in reflection_cases() if c['case_id'] == policy.reflection_case_id), None)
    if physical is None or reflection is None:
        return False
    if policy != reflection_execution_policy(physical, reflection):
        return False
    if not scene_supported(scene.get('scene_kind'), scene.get('conditional_input_policy')):
        return False
    if scene.get('conditional_input_policy') != execution_policy(physical):
        return False
    return (wall.get('slope_from_vertical_deg') == reflection['slope_from_vertical_deg']
            and wall.get('absorption_by_band') == reflection['absorption_by_band']
            and wall.get('unfolded_geometry_confirmed') is True
            and all(wall.get(key) == {s: reflection[key] for s in ('homogeneous', 'favourable')}
                    for key in ('incident_view_height_m', 'incident_view_width_m')))
