"""Smoke tests for the frontend milestone's backing services."""
from datetime import datetime, timedelta
import sqlite3

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from database.db import Base
from services import authentication, assignment_service, student_service, submission_service, storage_utils
from services.dates import LOCAL_ZONE
from services.rules_service import validate_rules, example_rules_bytes


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "grade.db"}')
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    for module in [authentication, assignment_service, student_service, submission_service]:
        monkeypatch.setattr(module, 'SessionLocal', session_factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')
    teacher = authentication.create_user('teacher_test', 'teacher-pass-123', 'teacher')
    return teacher, session_factory, tmp_path


def future_deadline():
    return datetime.now(LOCAL_ZONE) + timedelta(days=3)


def create_example(workspace):
    teacher, _, _ = workspace
    return assignment_service.create_assignment(
        teacher, 'Add two numbers', 'Submit a C file.', future_deadline(),
        'assignment.txt', b'Add two integers.', 'rules.json', example_rules_bytes())


def test_complete_teacher_student_workflow(workspace):
    teacher, session_factory, base = workspace
    student = student_service.create_student(teacher, 'alice', 'student-pass-123', 'Alice Gupta', 'CS001', 'alice@example.edu')
    assert authentication.authenticate_user('alice', 'student-pass-123')['id'] == student
    assignment = create_example(workspace)
    assert (base / 'storage' / 'assignments').exists()
    all_assignments = assignment_service.get_all_assignments(teacher)
    assert len(all_assignments) == 1
    assert all_assignments[0]['rules_hash']
    assert assignment_service.download_assignment_document(assignment, student) == b'Add two integers.'
    assert assignment_service.download_assignment_rules(assignment, teacher) == example_rules_bytes()
    result = submission_service.save_submission(student, assignment, 'solution.c', b'int main(){return 0;}\n')
    assert set(result) == {'submission_id', 'assignment_id'}
    student_history = submission_service.get_student_submissions(student)
    assert len(student_history) == 1
    assert 'source_hash' not in student_history[0]
    teacher_history = submission_service.get_teacher_submissions(teacher)
    assert len(teacher_history) == 1
    assert teacher_history[0]['source_hash']
    assert submission_service.get_submission_source(teacher, result['submission_id']) == b'int main(){return 0;}\n'
    student_service.update_student(teacher, student, 'alice2', 'Alice G', 'CS002', 'alice2@example.edu', True, 'new-student-pass-456')
    assert authentication.authenticate_user('alice2', 'new-student-pass-456')['id'] == student
    student_service.update_student(teacher, student, 'alice2', 'Alice G', 'CS002', 'alice2@example.edu', False)
    assert authentication.authenticate_user('alice2', 'new-student-pass-456') is None
    with pytest.raises(PermissionError):
        submission_service.save_submission(student, assignment, 'solution.c', b'int main(){}')
    student_service.delete_student(teacher, student)
    assert len(student_service.list_students(teacher)) == 0
    assert len(submission_service.get_teacher_submissions(teacher)) == 0


def test_deadline_rules_and_ownership(workspace):
    teacher, _, base = workspace
    student = student_service.create_student(teacher, 'bob', 'student-pass-123', 'Bob')
    another_teacher = authentication.create_user('another_teacher', 'other-pass-1234', 'teacher')
    assignment = create_example(workspace)
    with pytest.raises(PermissionError):
        assignment_service.download_assignment_rules(assignment, student)
    with pytest.raises(PermissionError):
        assignment_service.download_assignment_rules(assignment, another_teacher)
    with pytest.raises(PermissionError):
        assignment_service.update_assignment(another_teacher, assignment, 'Changed', 'fake', future_deadline())
    with pytest.raises(ValueError, match='weights'):
        validate_rules(b'{"time_limit_s":1,"memory_limit_mb":64,"metrics":[{"name":"correctness","analyzer":"io_tests","weight":0.2}]}')
    # Instructor may close an assignment by setting a past date.
    past = datetime.now(LOCAL_ZONE) - timedelta(days=1)
    assignment_service.update_assignment(teacher, assignment, 'Add numbers', 'Updated notes', past,
                                         'new_assignment.txt', b'updated', 'updated_rules.json', example_rules_bytes())
    assert assignment_service.download_assignment_document(assignment, student) == b'updated'
    with pytest.raises(ValueError, match='deadline'):
        submission_service.save_submission(student, assignment, 'solution.c', b'int main(){}')


def test_migration_preserves_existing_rows(tmp_path, monkeypatch):
    from database import init_db
    legacy_path = tmp_path / 'old.db'
    db = sqlite3.connect(legacy_path)
    db.executescript('''
       CREATE TABLE users (id INTEGER PRIMARY KEY, username VARCHAR(100) NOT NULL UNIQUE, password_hash VARCHAR(255) NOT NULL, role VARCHAR(20) NOT NULL);
       CREATE TABLE assignments (id INTEGER PRIMARY KEY, title VARCHAR(200) NOT NULL, description TEXT NOT NULL, created_by INTEGER NOT NULL, created_at DATETIME NOT NULL);
       CREATE TABLE submissions (id INTEGER PRIMARY KEY, student_id INTEGER NOT NULL, assignment_id INTEGER NOT NULL, source_path VARCHAR(500) NOT NULL, source_hash VARCHAR(64) NOT NULL, created_at DATETIME NOT NULL);
       INSERT INTO users VALUES (1, 'existing_user', 'oldhash', 'teacher');
       INSERT INTO assignments VALUES (1, 'Old assignment', 'Original text', 1, '2026-10-22 00:00:00');
    ''')
    db.commit()
    db.close()
    engine = create_engine(f'sqlite:///{legacy_path}')
    monkeypatch.setattr(init_db, 'engine', engine)
    init_db.initialize_database()
    init_db.initialize_database()  # Migration is idempotent.
    db = sqlite3.connect(legacy_path)
    assert db.execute('select username from users where id=1').fetchone()[0] == 'existing_user'
    assert db.execute('select title from assignments where id=1').fetchone()[0] == 'Old assignment'
    assert {'deadline_at', 'rules_path', 'document_path'}.issubset({r[1] for r in db.execute('pragma table_info(assignments)')})
    assert {'full_name', 'student_number', 'is_active'}.issubset({r[1] for r in db.execute('pragma table_info(users)')})
    db.close()
