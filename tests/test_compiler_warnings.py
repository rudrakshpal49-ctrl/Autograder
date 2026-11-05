"""Syntax analyzer tests use saved compiler evidence; native execution is not needed."""

import pytest

from agent.analyzers import HANDLERS, analyze_compiler_warnings
from agent.coordinator import dispatch_analyzers
from services.rules_service import validate_rules


def make_first_pass(compile_status='passed', smoke_status='passed', diagnostics='', warnings=0):
    return {
        'status': 'ready' if smoke_status == 'passed' else 'blocked',
        'compiler': {
            'status': compile_status,
            'diagnostics': diagnostics,
            'warning_count': warnings,
        },
        'smoke': {'status': smoke_status},
    }


def test_clean_code_gets_full_score():
    first_pass = make_first_pass()
    result = analyze_compiler_warnings({'first_pass': first_pass}, {})
    assert result['score'] == 1.0
    assert result['evidence']['compiled'] is True
    assert result['evidence']['diagnostics'] == []


def test_warnings_reduce_score_and_keep_details():
    first_pass = make_first_pass(diagnostics=(
        'student.c:4:9: warning: unused variable x [-Wunused-variable]\n'
        'student.c:5:9: warning: unused variable y [-Wunused-variable]\n'), warnings=2)
    result = analyze_compiler_warnings({'first_pass': first_pass}, {})
    assert result['score'] == 0.8
    assert result['evidence']['warning_count'] == 2
    assert len(result['evidence']['diagnostics']) == 2
    assert '2 warning(s)' in result['feedback']


def test_instructor_can_allow_one_warning_without_penalty():
    first_pass = make_first_pass(warnings=2)
    result = analyze_compiler_warnings(
        {'first_pass': first_pass}, {'max_warnings': 1, 'penalty_per_warning': 0.25})
    assert result['score'] == 0.75


def test_compile_failure_gets_zero_and_includes_error():
    first_pass = make_first_pass(compile_status='failed', diagnostics=(
        'student.c:2:3: error: expected ";" before "return"\n'))
    result = analyze_compiler_warnings({'first_pass': first_pass}, {})
    assert result['score'] == 0.0
    assert result['evidence']['compiled'] is False
    assert 'error:' in result['evidence']['diagnostics'][0]


def test_warning_deduction_never_goes_below_zero():
    first_pass = make_first_pass(warnings=20)
    assert analyze_compiler_warnings({'first_pass': first_pass}, {})['score'] == 0.0


def test_source_only_syntax_runs_even_if_smoke_times_out():
    first_pass = make_first_pass(smoke_status='timeout', warnings=1)
    metrics = [
        {'name': 'correctness', 'analyzer': 'io_tests'},
        {'name': 'syntax', 'analyzer': 'compiler_warnings', 'params': {}},
    ]
    report = dispatch_analyzers(first_pass, metrics, HANDLERS, {'first_pass': first_pass})
    assert report['status'] == 'partial'
    assert report['flags'] == ['suspected_infinite_loop']
    assert report['analyzers'][0]['status'] == 'skipped'
    assert report['analyzers'][1]['status'] == 'completed'
    assert report['analyzers'][1]['result']['score'] == 0.9


def test_example_rules_can_be_loaded():
    from pathlib import Path
    rules_file = Path(__file__).resolve().parents[1] / 'rules' / 'syntax_rules.json'
    rules = validate_rules(rules_file.read_bytes())
    assert rules['metrics'][0]['analyzer'] == 'compiler_warnings'


@pytest.mark.parametrize('params', [
    {'max_warnings': -1}, {'max_warnings': True},
    {'penalty_per_warning': -0.1}, {'penalty_per_warning': 1.1},
    {'penalty_per_warning': float('nan')},
])
def test_invalid_parameters_are_rejected(params):
    with pytest.raises(ValueError):
        analyze_compiler_warnings({'first_pass': make_first_pass()}, params)


def test_legacy_custom_gcc_flags_do_not_stop_syntax_grading():
    result = analyze_compiler_warnings(
        {'first_pass': make_first_pass(warnings=1)},
        {'flags': '-Wall -Wextra -Werror -std=c11'},
    )
    assert result['score'] == 0.9
    assert 'flags were ignored' in result['feedback']
