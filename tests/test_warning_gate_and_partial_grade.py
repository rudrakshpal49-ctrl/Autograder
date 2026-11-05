"""Requirements: N/A must not block scoring; first-pass warning -> execution 0."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from services import (authentication, assignment_service, first_pass_service,
                      student_service, storage_utils, submission_service)
from services.dates import LOCAL_ZONE
from services.final_grade_service import calculate_final_grade
from agent import first_pass, sandbox
from agent.coordinator import dispatch_analyzers
from agent.analyzers import HANDLERS


def test_partial_grade_normalizes_only_na_not_blocked_zero():
    rules = {'metrics': [
        {'name': 'Correctness', 'analyzer': 'io_tests', 'weight': .30},
        {'name': 'Memory', 'analyzer': 'sanitizer_run', 'weight': .20},
        {'name': 'Syntax', 'analyzer': 'compiler_warnings', 'weight': .30},
        {'name': 'Originality', 'analyzer': 'similarity_flag', 'weight': .20},
    ]}
    report = {
        'correctness_grading': {'status': 'skipped', 'score': 0.0,
                                'evidence': {'first_pass_blocked': True}},
        'memory_safety_grading': {'status': 'skipped', 'score': 0.0,
                                  'evidence': {'first_pass_blocked': True}},
        'syntax_grading': {'status': 'completed', 'score': .8},
        'originality_grading': {'status': 'insufficient_evidence', 'score': None},
    }
    result = calculate_final_grade(report, rules)
    assert result['score'] == 30.0  # 24 points earned / 80% available
    assert result['graded_weight'] == pytest.approx(.8)
    assert result['excluded_weight'] == pytest.approx(.2)
    assert result['status'] == 'provisional'
    assert result['breakdown'][0]['contribution'] == 0
    assert result['breakdown'][1]['contribution'] == 0

    # Ordinary skipped or unavailable analyzers do not get a zero without
    # an explicit first-pass reason.
    report['correctness_grading']['evidence'] = {}
    result = calculate_final_grade(report, rules)
    assert result['score'] == 48.0  # 24 points / .5
    assert len(result['missing']) == 2


def test_bad_rubric_still_no_grade():
    report = {'syntax_grading': {'status': 'completed', 'score': 1}}
    rules = {'metrics': [{'name': 'Syntax', 'analyzer': 'compiler_warnings', 'weight': .3}]}
    assert calculate_final_grade(report, rules)['score'] is None
    rules['metrics'][0]['weight'] = 1
    assert calculate_final_grade(report, rules)['score'] == 100


@pytest.mark.parametrize('compiler_warnings,static_status', [(1, 'passed'), (0, 'warning')])
def test_first_pass_warning_blocks_despite_successful_compile_and_smoke(monkeypatch,
                                                                         compiler_warnings,
                                                                         static_status):
    monkeypatch.setattr(first_pass, 'analyze_static_sanity', lambda src: {
        'status': static_status, 'checks': {}, 'message': 'warning' if static_status == 'warning' else 'ok',
    })
    monkeypatch.setattr(first_pass, 'compile_and_smoke', lambda *a, **kw: {
        'compiler': {'status': 'passed', 'warning_count': compiler_warnings},
        'smoke': {'status': 'passed'}, 'compiler_id': 'gcc',
    })
    result = first_pass.evaluate_source(b'int main(void) {return 0;}', sample_input='')
    assert result['status'] == 'blocked'
    assert result['execution_gate']['allowed'] is False
    assert result['execution_gate']['reasons']


def test_dispatch_does_not_execute_when_first_pass_warns():
    calls = []
    analyzers = ['io_tests', 'edge_case_runner', 'sanitizer_run',
                 'complexity_estimator', 'compiler_warnings']
    handlers = {name: (lambda ctx, params, name=name: calls.append(name) or {'score': 1})
                for name in analyzers}
    report = {'status': 'ready', 'compiler': {'status': 'passed', 'warning_count': 1},
              'static_sanity': {'status': 'passed'}, 'smoke': {'status': 'passed'}}
    dispatched = dispatch_analyzers(report,
        [{'name': name, 'analyzer': name} for name in analyzers], handlers, {})
    assert calls == ['compiler_warnings']
    blocked = dispatched['analyzers'][:4]
    assert all(entry['status'] == 'skipped' for entry in blocked)
    assert all(entry['result']['score'] == 0 for entry in blocked)


@pytest.mark.parametrize('compiler_warnings,static_status', [(1, 'passed'), (0, 'warning')])
def test_persisted_first_pass_warning_zeros_four_without_running(monkeypatch, tmp_path,
                                                                  compiler_warnings, static_status):
    engine = create_engine(f'sqlite:///{tmp_path / "grading.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (authentication, assignment_service, student_service,
                   submission_service, first_pass_service):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')

    teacher = authentication.create_user('teacher', 'secretpass', 'teacher')
    student = student_service.create_student(teacher, 'stu1', 'secretpass', 'Student One')
    names = ['io_tests', 'edge_case_runner', 'sanitizer_run',
             'complexity_estimator', 'compiler_warnings']
    import json
    rules = {'time_limit_s': 1, 'memory_limit_mb': 64, 'metrics': [
        {'name': name, 'analyzer': name, 'weight': 0.2,
         'params': {'required': 'O(N)'} if name == 'complexity_estimator' else {}}
        for name in names]}
    assignment = assignment_service.create_assignment(
        teacher, 'Warning grade', 'Description', datetime.now(LOCAL_ZONE) + timedelta(days=1),
        'task.txt', b'test', 'rules.json', json.dumps(rules).encode(), '1\n')
    sid = submission_service.save_submission(student, assignment, 'example.c',
                                              b'int main(void) {return 0;}')['submission_id']
    monkeypatch.setattr(first_pass_service, 'evaluate_source', lambda *a, **kw: {
        'status': 'ready',
        'compiler': {'status': 'passed', 'warning_count': compiler_warnings, 'diagnostics': ''},
        'smoke': {'status': 'passed'},
        'static_sanity': {'status': static_status},
        'rules_hash': kw.get('rules_hash'),
    })
    def should_not_run(ctx, params):
        pytest.fail('An execution analyzer was called despite first-pass warning')
    for name in names[:-1]:
        monkeypatch.setitem(HANDLERS, name, should_not_run)
    monkeypatch.setitem(HANDLERS, 'compiler_warnings', lambda context, params: {
        'status': 'completed', 'score': .75, 'evidence': {}, 'feedback': 'syntax grade'})
    result = first_pass_service.run_first_pass(teacher, sid)
    assert result['status'] == 'blocked'
    for field in ('correctness_grading', 'logical_grading',
                  'memory_safety_grading', 'complexity_grading'):
        assert result[field]['status'] == 'skipped'
        assert result[field]['score'] == 0
        assert result[field]['evidence']['first_pass_blocked'] is True
    assert result['final_grade']['score'] == 15.0
    assert result['final_grade']['status'] == 'complete'
    saved = first_pass_service.get_first_pass(teacher, sid)
    assert saved['final_grade']['score'] == 15.0


def test_teacher_and_student_reports_show_blocked_zero(tmp_path):
    from datetime import timezone
    from services.report_service import _build_evaluation, render_txt_report
    from services.teacher_report_service import build_teacher_report

    now = datetime.now(timezone.utc)
    meta = {'submission_id': 7, 'student': 'Student', 'assignment': 'Task',
            'filename': 'task.c', 'evaluated_at': now}
    rubric = {'metrics': [
        {'name': 'correctness', 'analyzer': 'io_tests', 'weight': .4},
        {'name': 'syntax', 'analyzer': 'compiler_warnings', 'weight': .4},
        {'name': 'originality', 'analyzer': 'similarity_flag', 'weight': .2},
    ]}
    report = {'status': 'blocked', 'compiler': {'status': 'passed', 'warning_count': 1},
              'smoke': {'status': 'passed'},
              'correctness_grading': {
                  'status': 'skipped', 'score': 0.0,
                  'feedback': 'First pass warning; zero awarded.',
                  'evidence': {'first_pass_blocked': True}},
              'syntax_grading': {'status': 'completed', 'score': .5, 'feedback': 'Warning'},
              'originality_grading': {'status': 'insufficient_evidence', 'score': None}}
    student = _build_evaluation(meta, report, rubric)
    row = next(m for m in student['metrics'] if m['analyzer'] == 'io_tests')
    assert row['status'] == 'skipped' and row['score'] == 0
    assert row['contribution'] == 0
    assert student['overall_score'] == 25.0  # 20 points / 80% scored weight
    assert student['provisional'] is True
    txt = render_txt_report(student).decode()
    assert '25.0 / 100' in txt and '0.0/10' in txt
    teacher = build_teacher_report(meta, report, rubric)
    assert teacher['grade'] == 25.0 and teacher['grade_status'] == 'provisional'
    teacher_correctness = next(m for m in teacher['metrics'] if m['analyzer'] == 'io_tests')
    assert teacher_correctness['score'] == 0.0
