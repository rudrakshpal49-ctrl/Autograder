"""Teacher-only exports: authorization and consistent, persisted evidence."""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.orm_models import FirstPassResult
from services import (authentication, assignment_service, student_service,
                      submission_service, storage_utils, teacher_report_service)
from services.dates import LOCAL_ZONE


def test_teacher_json_access_and_evidence(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "teacher_json.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (authentication, assignment_service, student_service,
                   submission_service, teacher_report_service):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')
    # Same-name teachers from different assignments must never export each other's data.
    teacher = authentication.create_user('teacher_json', 'password123', 'teacher')
    outsider = authentication.create_user('teacher_other', 'password123', 'teacher')
    student = student_service.create_student(teacher, 'student_json', 'password123', 'Student JSON')
    rubrics = [
        {'name': 'correctness', 'analyzer': 'io_tests', 'weight': 0.6, 'params': {}},
        {'name': 'syntax', 'analyzer': 'compiler_warnings', 'weight': 0.4, 'params': {}},
    ]
    rule_bytes = json.dumps({'time_limit_s': 1, 'memory_limit_mb': 64,
                             'metrics': rubrics}).encode()
    assignment = assignment_service.create_assignment(
        teacher, 'Example assignment', '', datetime.now(LOCAL_ZONE) + timedelta(days=2),
        'task.txt', b'Find the sum', 'rules.json', rule_bytes, '1 2\n')
    submission_id = submission_service.save_submission(
        student, assignment, 'sum.c', b'int main(void) {return 0;}')['submission_id']

    assert teacher_report_service.get_teacher_report_json(teacher, submission_id) is None
    with factory() as session:
        session.add(FirstPassResult(submission_id=submission_id, status='ready',
            report_json=json.dumps({
                'status': 'ready', 'rules_hash': 'sha256:existing',
                'compiler': {'status': 'passed', 'diagnostics': ''},
                'smoke': {'status': 'passed', 'stdout': '3\n'},
                'static_sanity': {'status': 'passed'},
                'flags': [],
                'correctness_grading': {'status': 'completed', 'score': 0.5,
                    'feedback': '1 of 2 tests pass',
                    'evidence': {'results': [{'passed': False, 'stdin': '5 7\n',
                        'expected_output': '12\n', 'actual_output': '11\n'}]}},
                'syntax_grading': {'status': 'completed', 'score': 1.0,
                    'feedback': 'No compiler warnings',
                    'evidence': {'warning_count': 0}},
            })))
        session.commit()

    report_bytes = teacher_report_service.get_teacher_report_json(teacher, submission_id)
    data = json.loads(report_bytes)
    assert data['grade'] == pytest.approx(70.0)
    assert data['grade_status'] == 'complete'
    assert data['submission'] == 'sum.c'
    assert data['student'] == 'Student JSON'
    assert data['first_pass']['compiled'] is True
    assert data['first_pass']['smoke_ok'] is True
    correctness = next(item for item in data['metrics'] if item['analyzer'] == 'io_tests')
    assert correctness['weight'] == pytest.approx(0.6)
    assert correctness['contribution'] == pytest.approx(30.0)
    assert correctness['evidence']['results'][0]['expected_output'] == '12\n'
    assert report_bytes.endswith(b'\n')
    with pytest.raises(PermissionError):
        teacher_report_service.get_teacher_report_json(student, submission_id)
    with pytest.raises(PermissionError):
        teacher_report_service.get_teacher_report_json(outsider, submission_id)


def test_partial_historical_report_uses_available_weight_only():
    data = teacher_report_service.build_teacher_report(
        {'submission_id': 9, 'filename': 's.c', 'student': 'Student',
         'assignment': 'Sample', 'evaluated_at': datetime(2026, 11, 4),
         'rules_hash': 'abc'},
        {'status': 'blocked', 'compiler': {'status': 'failed'},
         'smoke': {'status': 'skipped'},
         'syntax_grading': {'status': 'completed', 'score': 1.0,
                            'feedback': 'Compiler warnings 0', 'evidence': {}}},
        {'metrics': [
            {'analyzer': 'io_tests', 'weight': .6},
            {'analyzer': 'compiler_warnings', 'weight': .4},
        ]})
    assert data['grade'] == 100.0
    assert data['grade_status'] == 'provisional'
    assert data['first_pass']['compiled'] is False
    assert data['metrics'][0]['contribution'] is None
    assert next(m for m in data['metrics'] if m['analyzer'] == 'compiler_warnings')['contribution'] == pytest.approx(40.)


def test_user_interface_does_not_offer_json_to_student():
    from pathlib import Path
    source = (Path(__file__).resolve().parents[1] / 'app.py').read_text(encoding='utf-8')
    student_part = source.split('def student_dashboard', 1)[1]
    assert 'get_teacher_report_json(' not in student_part
    assert 'student_txt_' in student_part and 'student_pdf_' in student_part
    assert "st.container(key='ag_topbar')" in source
    assert 'max-width:none !important' in source
