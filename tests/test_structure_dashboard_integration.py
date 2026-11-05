"""Structure scores are saved with first-pass results for teacher display."""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from services import (authentication, assignment_service, student_service,
                      submission_service, first_pass_service, storage_utils)
from services.dates import LOCAL_ZONE
from services.first_pass_service import attach_structure_grading
from agent.analyzers import static_analysis


def fake_report(smoke='passed'):
    return {'status': 'ready' if smoke == 'passed' else 'blocked',
            'compiler': {'status': 'passed', 'warning_count': 0, 'diagnostics': ''},
            'smoke': {'status': smoke}, 'static_sanity': {'status': 'passed'}}


def use_fake_tools(monkeypatch):
    monkeypatch.setattr(static_analysis, '_functions_from_lizard', lambda source: [
        {'name': 'main', 'start_line': 1, 'end_line': 10, 'length': 10, 'cyclomatic': 3}])
    monkeypatch.setattr(static_analysis, '_cppcheck_diagnostics',
                        lambda source: {'status': 'not_installed', 'issues': [],
                                        'message': 'Cppcheck not installed.'})


def test_structure_preview_and_timeout(monkeypatch):
    use_fake_tools(monkeypatch)
    report = fake_report('timeout')
    attach_structure_grading(report, {'time_limit_s': 1, 'memory_limit_mb': 64},
                             b'int main(void){return 0;}')
    assert report['structure_grading']['status'] == 'completed'
    assert report['structure_grading']['score'] == 1.0
    assert report['structure_grading']['rule_source'] == 'default_preview'


def test_only_instructor_enabled_analyzers_scored(monkeypatch):
    use_fake_tools(monkeypatch)
    report = fake_report()
    attach_structure_grading(report, {'metrics': [
        {'name': 'syntax', 'analyzer': 'compiler_warnings'}]}, b'int main(){}')
    assert report['structure_grading']['status'] == 'not_configured'
    report = fake_report()
    attach_structure_grading(report, {'metrics': [
        {'name': 'structure', 'analyzer': 'static_analysis', 'weight': 0.4,
         'params': {'max_cyclomatic': 2}}]}, b'int main(){}')
    assert report['structure_grading']['score'] == 0.9
    assert report['structure_grading']['weight'] == 0.4


def test_structure_saved_and_readable_by_teacher(tmp_path, monkeypatch):
    use_fake_tools(monkeypatch)
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
        b'{"time_limit_s":1,"memory_limit_mb":64}', '5 7\n')
    submission = submission_service.save_submission(
        student, assignment, 'add.c', b'int main(void){return 0;}')['submission_id']
    monkeypatch.setattr(first_pass_service, 'evaluate_source',
                        lambda *args, **kwargs: fake_report('timeout'))
    assert first_pass_service.run_first_pass(student, submission) == {'submission_id': submission}
    saved = first_pass_service.get_first_pass(teacher, submission)
    assert saved['structure_grading']['status'] == 'completed'
    assert saved['structure_grading']['score'] == 1.0
