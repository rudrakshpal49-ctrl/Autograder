"""Syntax scoring is stored for the teacher dashboard after each first pass."""
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from services import (authentication, assignment_service, student_service,
                      submission_service, first_pass_service, storage_utils)
from services.dates import LOCAL_ZONE
from services.first_pass_service import attach_syntax_grading


def fake_report(*, smoke='passed', compiler='passed', warnings=2):
    return {
        'status': 'ready' if smoke == 'passed' else 'blocked',
        'compiler': {
            'status': compiler,
            'warning_count': warnings,
            'diagnostics': 'student.c:3:5: warning: unused variable x',
        },
        'smoke': {'status': smoke},
        'static_sanity': {'status': 'passed'},
    }


def test_syntax_scored_when_configured_by_instructor():
    report = fake_report(warnings=2)
    attach_syntax_grading(report, {'metrics': [{
        'name': 'syntax', 'analyzer': 'compiler_warnings', 'weight': 0.1,
        'params': {'max_warnings': 1, 'penalty_per_warning': 0.2},
    }]})
    syntax = report['syntax_grading']
    assert syntax['status'] == 'completed'
    assert syntax['score'] == pytest.approx(0.8)
    assert syntax['weight'] == 0.1
    assert syntax['rule_source'] == 'instructor'


def test_syntax_preview_scored_for_old_assignment_rules():
    report = fake_report()
    attach_syntax_grading(report, {'time_limit_s': 1, 'memory_limit_mb': 64})
    assert report['syntax_grading']['score'] == 0.8
    assert report['syntax_grading']['rule_source'] == 'default_preview'
    assert report['syntax_grading']['weight'] is None


def test_syntax_still_runs_after_smoke_timeout():
    report = fake_report(smoke='timeout', warnings=1)
    attach_syntax_grading(report, {})
    assert report['syntax_grading']['status'] == 'completed'
    assert report['syntax_grading']['score'] == 0.9


def test_failed_compilation_is_zero_syntax_score():
    report = fake_report(smoke='skipped', compiler='failed')
    attach_syntax_grading(report, {})
    assert report['syntax_grading']['score'] == 0


def test_syntax_not_scored_if_instructor_did_not_enable_it():
    report = fake_report()
    attach_syntax_grading(report, {'metrics': [{'name': 'correctness', 'analyzer': 'io_tests'}]})
    assert report['syntax_grading']['status'] == 'not_configured'


def test_syntax_result_persisted_and_teacher_can_read_it(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "grade.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (authentication, assignment_service, student_service,
                   submission_service, first_pass_service):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')

    teacher = authentication.create_user('teacher', 'secure-teacher-pass', 'teacher')
    student = student_service.create_student(teacher, 'student', 'secure-student-pass', 'Student')
    assignment = assignment_service.create_assignment(
        teacher, 'Add', 'Add integers', datetime.now(LOCAL_ZONE) + timedelta(days=2),
        'task.txt', b'Add two numbers', 'rules.json',
        b'{"time_limit_s": 1, "memory_limit_mb": 64}', '5 7\n')
    submission = submission_service.save_submission(
        student, assignment, 'add.c', b'int main(void){return 0;}')['submission_id']
    monkeypatch.setattr(first_pass_service, 'evaluate_source', lambda *args, **kwargs: fake_report(smoke='timeout'))

    private_result = first_pass_service.run_first_pass(student, submission)
    assert private_result == {'submission_id': submission}
    saved = first_pass_service.get_first_pass(teacher, submission)
    assert saved['syntax_grading']['status'] == 'completed'
    assert saved['syntax_grading']['score'] == 0.8
    with pytest.raises(PermissionError):
        first_pass_service.get_first_pass(student, submission)


def test_dashboard_syntax_scoring_with_legacy_gcc_flags():
    report = fake_report(warnings=1)
    attach_syntax_grading(report, {'metrics': [{
        'name': 'syntax', 'analyzer': 'compiler_warnings', 'weight': 0.1,
        'params': {'flags': '-Wall -Wextra -Werror -std=c11'},
    }]})
    assert report['syntax_grading']['status'] == 'completed'
    assert report['syntax_grading']['score'] == pytest.approx(0.9)
    assert 'flags were ignored' in report['syntax_grading']['feedback']
