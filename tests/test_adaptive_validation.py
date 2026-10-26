"""A finer check may discover an unknown after the primary diagnostic passed."""
import copy
import importlib.util
from pathlib import Path
import pytest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('adaptive_receipt',ROOT/'scripts/94_record_adaptive_validation.py')
receipt=importlib.util.module_from_spec(spec);spec.loader.exec_module(receipt)

def primary():
    return [dict(link_id='road',physical_case_id=case,integration_stage='adaptive_refined',
        integration_accuracy_outstanding=False,status='conditional_direct_component_numerical_diagnostic_met',
        whole_road_relative_energy=[[[1.]*8]*3]*2) for case in ('baseline','combined_reference')]

def check(unknown=False,change=0.):
    return dict(link_id='road',physical_case_id='combined_reference',
        status='finer_spacing_check_withheld_or_changed' if unknown or change>.1 else 'finer_spacing_check_passed',
        unknown_reason_counts={'unsupported_circular_envelope':1} if unknown else {},
        maximum_absolute_change_db=None if unknown else change,
        whole_road_relative_energy=None if unknown else [[[2.]*8]*3]*2)

@pytest.mark.parametrize('result',[check(unknown=True),check(change=3.0103)],ids=['new_unknown','large_spacing_change'])
def test_finer_failure_removes_primary_value_without_fallback(result):
    rows=primary();saved=copy.deepcopy(rows)
    final=receipt.postcheck_ledger(rows,[result])
    assert rows==saved
    assert final[1]['whole_road_relative_energy'] is None
    assert final[1]['integration_accuracy_outstanding']
    assert final[1]['status']=='withheld_after_finer_spacing_check'
    assert final[1]['finer_spacing_unknown_reason_counts']==result['unknown_reason_counts']
    assert final[0]['whole_road_relative_energy']==saved[0]['whole_road_relative_energy']
    assert final[0]['finer_spacing_check_status']=='not_selected'

def test_passing_check_preserves_primary_energy_and_unchecked_status():
    rows=primary();final=receipt.postcheck_ledger(rows,[check(change=.001)])
    assert final[1]['whole_road_relative_energy']==rows[1]['whole_road_relative_energy']
    assert final[1]['whole_road_relative_energy']!=check()['whole_road_relative_energy']
    assert final[1]['finer_spacing_check_status']=='finer_spacing_check_passed'
    assert not final[1]['integration_accuracy_outstanding']
    assert final[0]['finer_spacing_check_status']=='not_selected'

def test_check_cannot_resurrect_an_already_withheld_primary_road():
    rows=primary();rows[1].update(whole_road_relative_energy=None,integration_accuracy_outstanding=True)
    with pytest.raises(ValueError,match='primary diagnostic met'):receipt.postcheck_ledger(rows,[check()])

def test_duplicate_checks_are_rejected_instead_of_last_one_winning():
    with pytest.raises(ValueError,match='Duplicate'):receipt.postcheck_ledger(primary(),[check(unknown=True),check()])

def test_foreign_case_cannot_be_mistaken_for_another_case_on_same_road():
    result=check();result['physical_case_id']='unexecuted'
    with pytest.raises(ValueError,match='outside primary'):receipt.postcheck_ledger(primary(),[result])
