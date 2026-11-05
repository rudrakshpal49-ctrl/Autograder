"""Regrading replaces stale legacy correctness and logical evidence."""
import json
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.orm_models import FirstPassResult
from services import (authentication, assignment_service, student_service,
                      submission_service, first_pass_service, storage_utils)
from services.dates import LOCAL_ZONE


def test_rerun_replaces_legacy_correctness_and_logical_scores(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "clean.db"}')
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    for module in (authentication, assignment_service, student_service,
                   submission_service, first_pass_service):
        monkeypatch.setattr(module, 'SessionLocal', sessions)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')

    teacher = authentication.create_user('reset_teacher', 'password-1234', 'teacher')
    student = student_service.create_student(teacher, 'reset_student', 'password-1234', 'Student')
    assignment = assignment_service.create_assignment(
        teacher, 'Reset test', 'Instructions',
        datetime.now(LOCAL_ZONE) + timedelta(days=1),
        'assignment.txt', b'Test task', 'rules.json',
        b'{"time_limit_s":1,"memory_limit_mb":64,"metrics":['
        b'{"name":"syntax","analyzer":"compiler_warnings","weight":1.0}]}',
    )
    submission = submission_service.save_submission(
        student, assignment, 'solution.c', b'int main(void) { return 0; }')['submission_id']
    with sessions() as session:
        session.add(FirstPassResult(
            submission_id=submission, status='ready',
            report_json=json.dumps({'correctness_grading': {'score': 1.0},
                                    'logical_grading': {'score': 0.5}})))
        session.commit()

    seen = []
    def fake_first_pass(source, **kwargs):
        seen.append(kwargs['sample_input'])
        return {'status': 'incomplete',
                'compiler': {'status': 'passed', 'diagnostics': '', 'warning_count': 0},
                'smoke': {'status': 'skipped'},
                'static_sanity': {'status': 'passed'}}

    monkeypatch.setattr(first_pass_service, 'evaluate_source', fake_first_pass)
    first_pass_service.run_first_pass(teacher, submission)
    report = first_pass_service.get_first_pass(teacher, submission)
    assert seen == [None]  # No fallback to the obsolete approved-test service.
    assert report['syntax_grading']['score'] == 1.0
    assert report['correctness_grading']['status'] == 'skipped'
    assert report['logical_grading']['status'] == 'skipped'
    assert report['correctness_grading']['score'] == 0.0
    assert report['smoke']['status'] == 'skipped'
