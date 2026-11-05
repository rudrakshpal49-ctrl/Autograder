"""Focused tests for the new opt-in Callgrind complexity grading."""
from __future__ import annotations

from datetime import datetime, timezone
import json

import pytest

from agent.analyzers import HANDLERS
from agent.analyzers import time_growth_bridge as bridge
from services import first_pass_service, report_service, teacher_report_service
from services.rules_service import validate_rules, rules_warnings


def growth_rules(**params):
    return json.dumps({
        'time_limit_s': 1, 'memory_limit_mb': 64,
        'metrics': [{'analyzer': 'complexity_estimator', 'name': 'complexity',
                     'weight': 1.0, 'params': {'required': 'O(N)', **params}}]
    }).encode()


def ready_first_pass():
    return {'status': 'ready', 'smoke': {'status': 'passed'}}


def test_registered_under_old_rubric_id_with_new_python_filenames():
    assert 'complexity_estimator' in HANDLERS
    assert HANDLERS['complexity_estimator'] is bridge.analyze_time_growth
    assert bridge.analyze_time_growth.__module__.endswith('time_growth_bridge')
    assert validate_rules(growth_rules())['metrics'][0]['weight'] == 1.0
    assert not any('reserved' in warning or 'not yet implemented' in warning
                   for warning in rules_warnings(validate_rules(growth_rules())))


@pytest.mark.parametrize('changes', [
    {'required': 'O(invented)'},
    {'input_sizes': [2, 2, 4, 8, 16]},
    {'input_sizes': [2, 4, 8]},
    {'max_measurements': 4},
    {'min_successful_measurements': 9, 'max_measurements': 6},
    {'adaptive_max_n': -5},
    {'ollama_complexity_fallback': 'yes'},
    {'optimization': '-Ofast'},
])
def test_rejects_bad_complexity_configurations(changes):
    with pytest.raises(ValueError):
        validate_rules(growth_rules(**changes))


def test_unavailable_callgrind_is_not_a_zero_grade(monkeypatch):
    monkeypatch.setattr(bridge.shutil, 'which', lambda n: None)
    monkeypatch.setattr(bridge.profiler, '_callgrind_header', lambda: None)
    result = bridge.analyze_time_growth({'source': b'int main(void) {return 0;}'},
                                        {'required': 'O(N)'})
    assert result['status'] == 'unavailable'
    assert result['score'] is None
    assert 'Callgrind' in result['feedback']


def _simulated_profile(fit, empirical, **kwargs):
    return {
        'score': 1.0,
        'feedback': 'synthetic result',
        'evidence': {
            'fit': fit,
            'detected_complexity': kwargs.get('detected', empirical),
            'empirical_detected_complexity': empirical,
            'complexity_estimate_source': kwargs.get('source', 'Callgrind model fitting'),
            'profiling_method': 'callgrind_instructions',
            'trials': [{'n': n, 'cost': n*n,
                        'method': 'callgrind_instructions'} for n in (4, 8, 16, 32, 64)],
        },
    }


def enable_profile(monkeypatch, result):
    monkeypatch.setattr(bridge.shutil, 'which', lambda n: '/usr/bin/valgrind')
    monkeypatch.setattr(bridge.profiler, '_callgrind_header', lambda: '/usr/include/valgrind/callgrind.h')
    monkeypatch.setattr(bridge.profiler, 'evaluate', lambda *a, **kw: result)


def test_grade_uses_only_confident_empirical_evidence(monkeypatch):
    enable_profile(monkeypatch, _simulated_profile({'confidence': 'high'}, 'O(N^2)'))
    result = bridge.analyze_time_growth({'source': b'int main(void){return 0;}'},
                                        {'required': 'O(N)'})
    assert result['status'] == 'completed'
    assert result['score'] == 0.4
    assert result['evidence']['score_basis'] == 'empirical_callgrind_only'
    enable_profile(monkeypatch, _simulated_profile({'confidence': 'medium'}, 'O(N)'))
    result = bridge.analyze_time_growth({'source': b'int main(void){return 0;}'},
                                        {'required': 'O(N^2)'})
    assert result['status'] == 'completed' and result['score'] == 1.0


def test_llm_advisory_never_becomes_a_grade(monkeypatch):
    enable_profile(monkeypatch, _simulated_profile(
        {'confidence': 'low', 'ollama_fallback': {'used': True, 'complexity': 'O(N)'}},
        'inconclusive', source='Ollama source-analysis fallback', detected='O(N)'))
    result = bridge.analyze_time_growth({'source': b'int main(void){return 0;}'},
                                        {'required': 'O(N)'})
    assert result['status'] == 'insufficient_evidence'
    assert result['score'] is None


def test_report_dispatches_and_skips_on_smoke_failure(monkeypatch):
    dispatched = []
    def fake_dispatch(report, metrics, handlers, context):
        dispatched.append(context)
        return {'analyzers': [{'status': 'completed', 'result': {
            'status': 'completed', 'score': 1.0, 'evidence': {}, 'feedback': 'O(N)'}}]}
    monkeypatch.setattr(first_pass_service, 'dispatch_analyzers', fake_dispatch)
    metric = {'name': 'complexity', 'analyzer': 'complexity_estimator',
              'weight': 0.1, 'params': {'required': 'O(N)'}}
    rules = {'time_limit_s': 1, 'memory_limit_mb': 64, 'metrics': [metric]}
    report = ready_first_pass()
    first_pass_service.attach_complexity_grading(report, rules, b'source', '5\n')
    assert report['complexity_grading']['score'] == 1.0
    assert report['complexity_grading']['weight'] == 0.1
    assert dispatched[0]['sample_input'] == '5\n'
    report = {'status': 'blocked', 'smoke': {'status': 'timeout'}}
    first_pass_service.attach_complexity_grading(report, rules, b'source', '5\n')
    assert report['complexity_grading']['status'] == 'skipped'
    assert report['complexity_grading']['score'] == 0.0
    assert report['complexity_grading']['evidence']['first_pass_blocked'] is True
    assert len(dispatched) == 1


def test_new_reports_include_complexity_in_json_and_student_summary():
    meta = {'submission_id': 3, 'student': 'Student A', 'filename': 'prog.c',
            'assignment': 'Test', 'evaluated_at': datetime.now(timezone.utc)}
    stored = {'status': 'ready', 'compiler': {'status': 'passed'},
              'smoke': {'status': 'passed'},
              'complexity_grading': {'status': 'completed', 'score': 1.,
                                     'feedback': 'Measured O(N)',
                                     'evidence': {'fit': {'confidence': 'medium'},
                                                  'empirical_detected_complexity': 'O(N)'}}}
    rubric = {'metrics': [{'analyzer': 'complexity_estimator', 'weight': 1.0}]}
    student = report_service._build_evaluation(meta, stored, rubric)
    assert student['overall_score'] == 100.0
    assert next(row for row in student['metrics'] if row['analyzer'] == 'complexity_estimator')['score'] == 1.0
    exported = teacher_report_service.build_teacher_report(meta, stored, rubric)
    assert exported['grade'] == 100.0
    complexity = next(row for row in exported['metrics'] if row['analyzer'] == 'complexity_estimator')
    assert complexity['evidence']['empirical_detected_complexity'] == 'O(N)'
    assert complexity['contribution'] == 100.0


def test_old_saved_report_still_has_no_fabricated_complexity_metric():
    meta = {'submission_id': 3, 'student': 'Student A', 'filename': 'prog.c',
            'assignment': 'Test', 'evaluated_at': datetime.now(timezone.utc)}
    stored = {'status': 'ready', 'compiler': {'status': 'passed'},
              'smoke': {'status': 'passed'}}
    data = report_service._build_evaluation(meta, stored, {
        'metrics': [{'analyzer': 'complexity_estimator', 'weight': 1.0}]})
    assert not any(row['analyzer'] == 'complexity_estimator' for row in data['metrics'])
    assert data['overall_score'] is None
