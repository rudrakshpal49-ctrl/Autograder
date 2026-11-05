"""Modularity results persist and remain available after smoke timeouts."""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from services import (authentication, assignment_service, student_service,
                      submission_service, first_pass_service, storage_utils)
from services.dates import LOCAL_ZONE
from services.first_pass_service import attach_modularity_grading
from agent.analyzers import function_split


def fake_report(smoke='passed'):
    return {'status': 'ready' if smoke == 'passed' else 'blocked',
            'compiler': {'status': 'passed', 'warning_count': 0, 'diagnostics': ''},
            'smoke': {'status': smoke}, 'static_sanity': {'status': 'passed'}}


def use_fake_lizard(monkeypatch, names=('main',)):
    monkeypatch.setattr(function_split, '_functions_from_lizard', lambda source: [
        {'name': name, 'start_line': 1, 'end_line': 4, 'length': 4, 'cyclomatic': 1}
        for name in names])


def test_preview_default_and_instructor_parameters(monkeypatch):
    use_fake_lizard(monkeypatch)
    report = fake_report('timeout')
    attach_modularity_grading(report, {'time_limit_s': 1, 'memory_limit_mb': 64}, b'int main(){}')
    assert report['modularity_grading']['status'] == 'completed'
    assert report['modularity_grading']['score'] == 1.0
    assert report['modularity_grading']['rule_source'] == 'default_preview'

    report = fake_report('timeout')
    attach_modularity_grading(report, {'metrics': [{
        'name': 'modularity', 'analyzer': 'function_split', 'weight': 0.1,
        'params': {'expect_helpers': True, 'min_functions': 2},
    }]}, b'int main(){}')
    assert report['modularity_grading']['score'] == 0.7
    assert report['modularity_grading']['weight'] == 0.1
    assert report['modularity_grading']['rule_source'] == 'instructor'


def test_not_configured_if_metrics_exclude_modularity(monkeypatch):
    use_fake_lizard(monkeypatch)
    report = fake_report()
    attach_modularity_grading(report, {'metrics': [
        {'name': 'syntax', 'analyzer': 'compiler_warnings'},
    ]}, b'int main(){}')
    assert report['modularity_grading']['status'] == 'not_configured'


def test_teacher_can_see_persisted_modularity_after_timeout(tmp_path, monkeypatch):
    use_fake_lizard(monkeypatch)
    engine = create_engine(f'sqlite:///{tmp_path / "modularity.db"}')
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
        teacher, 'Functions', 'Use helper functions',
        datetime.now(LOCAL_ZONE) + timedelta(days=2),
        'task.txt', b'Use helper functions', 'rules.json',
        b'{"time_limit_s":1,"memory_limit_mb":64,"metrics":[{"name":"modularity","analyzer":"function_split","weight":1.0,"params":{"expect_helpers":true,"min_functions":2}}]}',
        '5 7\n')
    submission = submission_service.save_submission(
        student, assignment, 'solution.c', b'int main(void){return 0;}')['submission_id']
    monkeypatch.setattr(first_pass_service, 'evaluate_source',
                        lambda *args, **kwargs: fake_report('timeout'))

    assert first_pass_service.run_first_pass(student, submission) == {'submission_id': submission}
    saved = first_pass_service.get_first_pass(teacher, submission)
    assert saved['modularity_grading']['status'] == 'completed'
    assert saved['modularity_grading']['score'] == 0.7
    assert saved['syntax_grading']['status'] == 'not_configured'
    assert saved['structure_grading']['status'] == 'not_configured'
