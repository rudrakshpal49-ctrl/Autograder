"""Cross-assignment isolation, report persistence, first-pass timeout dispatch."""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.analyzers import similarity_flag
from database.db import Base
from services import (authentication, assignment_service, student_service,
                      submission_service, first_pass_service, storage_utils)
from services.dates import LOCAL_ZONE
from tests.test_originality import SOURCE_A, SOURCE_B


def fake_report():
    return {
        'status': 'blocked',
        'compiler': {'status': 'passed', 'warning_count': 0, 'diagnostics': ''},
        'smoke': {'status': 'timeout'},
        'static_sanity': {'status': 'passed'},
    }


def test_source_only_runs_after_timeout_and_doesnt_compare_other_assignments(monkeypatch, tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "originality.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (authentication, assignment_service, student_service,
                   submission_service, first_pass_service):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')
    teacher = authentication.create_user('teacher_orig', 'secure-pass', 'teacher')
    student1 = student_service.create_student(teacher, 'student_one', 'secure-pass', 'First')
    student2 = student_service.create_student(teacher, 'student_two', 'secure-pass', 'Second')
    assignment1 = assignment_service.create_assignment(
        teacher, 'Assignment 1', '', datetime.now(LOCAL_ZONE) + timedelta(days=3),
        'task.txt', b'Find common elements',
        originality_enabled=True, originality_threshold=0.85, originality_use_ai=True)
    assignment2 = assignment_service.create_assignment(
        teacher, 'Assignment 2', '', datetime.now(LOCAL_ZONE) + timedelta(days=3),
        'task.txt', b'Find common elements',
        originality_enabled=True, originality_threshold=0.85, originality_use_ai=False)
    first = submission_service.save_submission(student1, assignment1, 'first.c', SOURCE_A.encode())['submission_id']
    second = submission_service.save_submission(student2, assignment1, 'second.c', SOURCE_B.encode())['submission_id']
    off_assignment = submission_service.save_submission(student2, assignment2, 'other.c', SOURCE_A.encode())['submission_id']
    monkeypatch.setattr(first_pass_service, 'evaluate_source', lambda *args, **kwargs: fake_report())
    monkeypatch.setattr(similarity_flag, '_request_ollama', lambda *args: {
        'overlap_score': 90, 'observations': ['Both use nested searches.']})

    first_pass_service.run_first_pass(student1, first)
    report = first_pass_service.get_first_pass(teacher, first)
    orig = report['originality_grading']
    assert orig['status'] == 'completed'
    assert orig['flagged'] is True
    assert orig['evidence']['peer_count'] == 1
    assert orig['evidence']['matches'][0]['submission_id'] == second
    assert orig['source_filename'] == 'first.c'
    assert orig['evidence']['matches'][0]['filename'] == 'second.c'
    assert orig['evidence']['matches'][0]['student_name'] == 'Second'
    assert orig['score'] == 0.02  # 1 - (0.8 * 1.0 + 0.2 * 0.9)
    assert orig['grade_status'] == 'provisional'
    assert orig['evidence']['review_priority'] == 0.98
    assert report['smoke']['status'] == 'timeout'
    assert off_assignment not in [m['submission_id'] for m in orig['evidence']['matches']]

    # Rerunning on an unchanged peer corpus reuses saved AI output.
    first_pass_service.run_first_pass(student1, first)
    again = first_pass_service.get_first_pass(teacher, first)
    assert again['originality_grading']['evidence']['ai']['cached'] is True

    first_pass_service.run_first_pass(student2, off_assignment)
    alone = first_pass_service.get_first_pass(teacher, off_assignment)
    assert alone['originality_grading']['status'] == 'insufficient_evidence'
    assert alone['originality_grading']['score'] is None

    # A student's run endpoint does not disclose peer IDs, code or report findings.
    assert first_pass_service.run_first_pass(student1, first) == {'submission_id': first}
