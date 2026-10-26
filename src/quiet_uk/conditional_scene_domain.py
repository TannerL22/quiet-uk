"""Explicit permission to evaluate a declared conditional real cross section.

This is a research scope declaration, not measurement or release qualification.
No scene is renamed synthetic/reference and defaults continue to refuse real data.
"""
from dataclasses import dataclass

from .physical_scene_assumptions import POLICY_ID, assumption_cases


@dataclass(frozen=True)
class ConditionalExecution:
    physical_policy_id: str
    physical_case_id: str
    path_scope: str
    real_area_validated: bool = False
    absolute_release_eligible: bool = False


def execution_policy(case):
    if not any(case == c for c in assumption_cases()):
        raise ValueError('Use an exact declared physical case, not a modified default')
    return ConditionalExecution(POLICY_ID,case['case_id'],'direct_only_reflections_omitted')


def scene_supported(kind, policy=None):
    if kind in ('synthetic','published_reference'):return True
    return (kind == 'conditional_real_input' and type(policy) is ConditionalExecution
        and policy.physical_policy_id == POLICY_ID
        and policy.physical_case_id in {c['case_id'] for c in assumption_cases()}
        and policy.path_scope == 'direct_only_reflections_omitted'
        and policy.real_area_validated is False and policy.absolute_release_eligible is False)
