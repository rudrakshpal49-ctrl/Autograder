"""Unit tests for source-only structure grading (no local execution required)."""
from types import SimpleNamespace
import sys

import pytest

from agent.analyzers import static_analysis
from agent.analyzers import HANDLERS
from agent.coordinator import dispatch_analyzers


def fake_lizard_functions(monkeypatch, functions):
    monkeypatch.setattr(static_analysis, '_functions_from_lizard', lambda source: functions)
    monkeypatch.setattr(static_analysis, '_cppcheck_diagnostics',
                        lambda source: {'status': 'not_installed', 'issues': [],
                                        'message': 'Cppcheck not found on PATH.'})


def test_simple_function_gets_full_score(monkeypatch):
    fake_lizard_functions(monkeypatch, [
        {'name': 'main', 'start_line': 1, 'end_line': 5, 'length': 5, 'cyclomatic': 1}])
    result = static_analysis.analyze_structure({'source': b'int main(){return 0;}'}, {})
    assert result['score'] == 1.0
    assert result['evidence']['function_count'] == 1
    assert result['evidence']['violation_count'] == 0
    assert 'Cppcheck not found' in result['feedback']


def test_thresholds_and_penalties(monkeypatch):
    fake_lizard_functions(monkeypatch, [
        {'name': 'foo', 'start_line': 1, 'end_line': 42, 'length': 42, 'cyclomatic': 9},
        {'name': 'bar', 'start_line': 45, 'end_line': 50, 'length': 6, 'cyclomatic': 2}])
    result = static_analysis.analyze_structure({'source': 'int foo(){}'}, {
        'max_cyclomatic': 8, 'max_function_length': 40,
        'penalty_per_violation': 0.2})
    assert result['score'] == pytest.approx(0.6)
    assert result['evidence']['violation_count'] == 2
    assert result['evidence']['max_function_length_found'] == 42
    assert result['evidence']['max_cyclomatic_found'] == 9


def test_score_is_clamped_and_missing_functions_not_full_marks(monkeypatch):
    fake_lizard_functions(monkeypatch, [
        {'name': 'foo', 'start_line': 1, 'end_line': 42, 'length': 42, 'cyclomatic': 9}])
    assert static_analysis.analyze_structure({'source': 'text'},
                                             {'penalty_per_violation': 1})['score'] == 0
    fake_lizard_functions(monkeypatch, [])
    assert static_analysis.analyze_structure({'source': 'text'}, {})['score'] == 0


@pytest.mark.parametrize('params', [
    {'max_cyclomatic': 0}, {'max_function_length': -1},
    {'max_function_length': True}, {'penalty_per_violation': 1.1},
    {'penalty_per_violation': float('nan')},
])
def test_invalid_rules_are_rejected(params):
    with pytest.raises(ValueError):
        static_analysis.analyze_structure({'source': 'int main(){}'}, params)


def test_lizard_api_reads_source_and_physical_function_length(monkeypatch):
    fake_module = SimpleNamespace(analyze_file=SimpleNamespace(
        analyze_source_code=lambda filename, text: SimpleNamespace(function_list=[
            SimpleNamespace(name='main', start_line=2, end_line=7,
                            cyclomatic_complexity=3)])))
    monkeypatch.setitem(sys.modules, 'lizard', fake_module)
    functions = static_analysis._functions_from_lizard('int main(){return 0;}')
    assert functions == [{'name': 'main', 'start_line': 2, 'end_line': 7,
                          'length': 6, 'cyclomatic': 3}]


def test_cppcheck_xml_is_parsed(monkeypatch):
    monkeypatch.setattr(static_analysis.shutil, 'which', lambda name: '/usr/bin/cppcheck')
    xml = ('<?xml version="1.0"?><results version="2">'
           '<errors><error severity="warning" id="unusedVariable" msg="Unused variable">'
           '<location file="submission.c" line="4"/></error></errors></results>')
    monkeypatch.setattr(static_analysis.subprocess, 'run', lambda *args, **kwargs:
                        SimpleNamespace(stderr=xml, returncode=0))
    result = static_analysis._cppcheck_diagnostics('int main(){}')
    assert result['status'] == 'completed'
    assert result['issues'][0] == {
        'severity': 'warning', 'id': 'unusedVariable',
        'message': 'Unused variable', 'line': 4}


def test_cppcheck_results_do_not_affect_lizard_score(monkeypatch):
    monkeypatch.setattr(static_analysis, '_functions_from_lizard', lambda source: [
        {'name': 'main', 'start_line': 1, 'end_line': 5, 'length': 5, 'cyclomatic': 1}])
    monkeypatch.setattr(static_analysis, '_cppcheck_diagnostics',
                        lambda source: {'status': 'completed',
                                        'issues': [{'severity': 'warning', 'message': 'example'}],
                                        'message': 'Cppcheck reported 1 finding.'})
    result = static_analysis.analyze_structure({'source': 'int main(){}'}, {})
    assert result['score'] == 1.0
    assert len(result['evidence']['cppcheck']['issues']) == 1


def test_structure_runs_after_smoke_timeout(monkeypatch):
    fake_lizard_functions(monkeypatch, [
        {'name': 'main', 'start_line': 1, 'end_line': 5, 'length': 5, 'cyclomatic': 1}])
    report = dispatch_analyzers(
        {'status': 'blocked', 'smoke': {'status': 'timeout'}},
        [{'name': 'structure', 'analyzer': 'static_analysis', 'params': {}}],
        HANDLERS, {'source': 'int main(){}'})
    assert report['status'] == 'partial'
    assert report['flags'] == ['suspected_infinite_loop']
    assert report['analyzers'][0]['status'] == 'completed'
    assert report['analyzers'][0]['result']['score'] == 1.0
