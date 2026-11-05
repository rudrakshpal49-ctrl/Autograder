"""Exercise supported rules, warnings, edge-category requests and grading behavior."""
import json
import pytest

from services.rules_service import (validate_rules, rules_warnings, get_edge_types,
                                    set_originality_preferences)
from agent.analyzers.io_tests import grade_cases
from agent.analyzers.edge_case_runner import grade_edge_cases
from agent.test_generation import request_candidates
from services.edge_scenarios import tag_rationale, edge_type_from_rationale
from agent.analyzers import similarity_flag


def rules_for(analyzer, params, **extra):
    return json.dumps(dict(time_limit_s=1, memory_limit_mb=64,
        metrics=[dict(name=analyzer, analyzer=analyzer, weight=0.5, params=params)],
        **extra)).encode()


def test_valid_logical_params_are_read_and_warnings_reveal_ignored_fields():
    settings = validate_rules(rules_for('edge_case_runner',
        {'cases': ['negatives', 'zero', 'int_overflow'], 'output_comparison': 'trim_trailing'}))
    assert get_edge_types(settings) == ['negatives', 'zero', 'int_overflow']
    assert not any('params.cases' in w for w in rules_warnings(settings))
    bad = validate_rules(rules_for('compiler_warnings',
        {'max_warnings': 1, 'flags': '-Werror', 'unsupported_threshold': 7}))
    assert any('flags' in w and 'ignored' in w for w in rules_warnings(bad))
    assert any('unsupported_threshold' in w and 'ignored' in w for w in rules_warnings(bad))


@pytest.mark.parametrize('analyzer,params', [
    ('edge_case_runner', {'cases': ['zero', 'zero']}),
    ('edge_case_runner', {'cases': ['Invalid Value']}),
    ('edge_case_runner', {'cases': []}),
    ('io_tests', {'output_comparison': 'invalid'}),
    ('io_tests', {'output_comparison': []}),
    ('compiler_warnings', {'max_warnings': -1}),
    ('function_split', {'expect_helpers': 'yes'}),
    ('static_analysis', {'max_function_length': -5}),
    ('similarity_flag', {'ai_weight': 2}),
    ('llm_review', {'model': []}),
    ('llm_review', {'max_line_length': 0}),
])
def test_rules_reject_invalid_config_before_assignment_is_saved(analyzer, params):
    with pytest.raises(ValueError):
        validate_rules(rules_for(analyzer, params))


def test_legacy_incomplete_metric_is_allowed_but_explained():
    data = validate_rules(b'{"time_limit_s":1,"memory_limit_mb":64,"metrics":[{"name":"old"}]}')
    assert any('unknown analyzer' in w for w in rules_warnings(data))


def test_trailing_output_comparison_is_opt_in_and_exact_still_default():
    cases = [dict(id=5, category='normal', source='teacher', title='Sample',
                  stdin='1 2\n', expected_output='1 2 3  ')]
    runner = lambda *args, **kw: dict(status='completed', runs=[dict(status='ok', stdout='1 2 3 \r\n')])
    opts = dict(time_limit_s=1, memory_limit_mb=64, runner=runner)
    assert grade_cases(b'', cases, **opts)['score'] == 0
    result = grade_cases(b'', cases, output_comparison='trim_trailing', **opts)
    assert result['score'] == 1
    assert result['evidence']['output_comparison'] == 'trim_trailing'
    with pytest.raises(ValueError):
        grade_cases(b'', cases, output_comparison='invalid', **opts)


def test_logical_grading_reports_requested_vs_covered_scenarios():
    cases = [dict(id=7, category='edge', source='qwen', stdin='0 3\n',
                  expected_output='3\n', rationale='[edge:zero] zero at start', title='Zero')]
    runner = lambda *args, **kw: dict(status='completed', runs=[dict(status='ok', stdout='3\n')])
    result = grade_edge_cases(b'', cases, time_limit_s=1, memory_limit_mb=64,
                              runner=runner, requested_edge_types=['zero', 'negatives'])
    assert result['score'] == 1
    assert result['evidence']['covered_edge_types'] == ['zero']
    assert result['evidence']['missing_edge_types'] == ['negatives']
    assert 'negatives' in result['feedback']
    assert edge_type_from_rationale(tag_rationale('some rationale', 'zero')) == 'zero'


def test_qwen_prompt_contains_teacher_categories_and_rejects_unknown_tags(monkeypatch):
    from agent import test_generation
    requests = []
    class FakeReply:
        def __enter__(self): return self
        def __exit__(self, *args): return False
        def read(self):
            return json.dumps({'response': json.dumps({'cases': [
                dict(stdin='0 5\n', title='Zero', rationale='zero scenario', edge_type='zero'),
                dict(stdin='9 2\n', title='Irrelevant', rationale='ordinary', edge_type='other'),
            ]})}).encode()
    def fake_urlopen(req, timeout):
        requests.append(json.loads(req.data))
        return FakeReply()
    monkeypatch.setattr(test_generation.urllib.request, 'urlopen', fake_urlopen)
    result = request_candidates('Add two integers', '1 2\n', '3\n', '', '',
                                'edge', 1, requested_edge_types=['zero', 'negatives'])
    assert len(result) == 1 and result[0]['edge_type'] == 'zero'
    assert 'Teacher-selected edge scenarios: zero, negatives' in requests[0]['prompt']
    assert 'edge_type' in requests[0]['prompt']


def test_uploaded_originality_ai_weight_survives_teacher_ui_choices():
    source = rules_for('similarity_flag', {'ai_weight': 0.65, 'flag_threshold': 0.85})
    updated = set_originality_preferences(source, True, 0.8, True)
    settings = validate_rules(updated)
    assert settings['metrics'][0]['params']['ai_weight'] == 0.65
    assert settings['metrics'][0]['params']['flag_threshold'] == 0.8


def test_originality_weight_drives_scoring_when_ai_review_available(monkeypatch):
    x = 'int main(){ int a=5; int b=8; return a+b; }'
    y = 'int main(){ int q=7; int w=9; return q-w; }'
    monkeypatch.setattr(similarity_flag, '_request_ollama', lambda *args: {
        'overlap_score': 10, 'observations': ['few similar structures']})
    context = {'source': x, 'peers': [{'submission_id': 2, 'source': y, 'filename': 'other.c'}]}
    zero = similarity_flag.analyze_originality(context, {'min_tokens': 10, 'ai_weight': 0.0})
    full = similarity_flag.analyze_originality(context, {'min_tokens': 10, 'ai_weight': 1.0})
    assert zero['status'] == full['status'] == 'completed'
    assert zero['evidence']['ai_weight'] == 0
    assert full['evidence']['ai_weight'] == 1
    assert full['score'] == 0.9
    assert zero['score'] != full['score']
