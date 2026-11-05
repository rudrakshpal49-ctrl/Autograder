"""Student downloads must be correct, private, and independent of grading logic."""
import json
from datetime import datetime, timedelta, timezone

import pytest
from pypdf import PdfReader
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.orm_models import FirstPassResult
from services import (authentication, assignment_service, student_service,
                      submission_service, storage_utils, report_service)
from services.dates import LOCAL_ZONE


def saved_report():
    return {
        'status': 'ready',
        'correctness_grading': {
            'status': 'completed', 'score': 0.5, 'feedback': '1 of 2 approved tests passed.',
            'evidence': {'results': [
                {'passed': True, 'stdin': '1 2\n', 'expected_output': '3\n',
                 'actual_output': '3\n', 'execution_status': 'ok'},
                {'passed': False, 'stdin': '4 5\n', 'expected_output': '9\n',
                 'actual_output': '8\n', 'execution_status': 'ok'},
            ]},
        },
        'syntax_grading': {'status': 'completed', 'score': 1.0,
                           'feedback': 'No compiler warnings.'},
        'originality_grading': {'status': 'completed', 'score': 0.5,
            'feedback': 'Maximum similarity against private_peer.c student #456',
            'evidence': {'matches': [{'student_name': 'SECRET_PEER',
                                     'filename': 'private_peer.c'}]}},
    }


def test_report_access_txt_and_pdf(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "report.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (authentication, assignment_service, student_service,
                   submission_service, report_service):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')

    teacher = authentication.create_user('report_teacher', 'password123', 'teacher')
    student = student_service.create_student(teacher, 'report_student', 'password123', 'Report Student')
    other = student_service.create_student(teacher, 'another_student', 'password123', 'Other Student')
    metrics = [{'name': 'correctness', 'analyzer': 'io_tests', 'weight': 0.6, 'params': {}},
               {'name': 'syntax', 'analyzer': 'compiler_warnings', 'weight': 0.4, 'params': {}}]
    rules = {'time_limit_s': 1, 'memory_limit_mb': 64, 'metrics': metrics}
    assignment = assignment_service.create_assignment(
        teacher, 'Add integers', '', datetime.now(LOCAL_ZONE) + timedelta(days=1),
        'task.txt', b'Add', 'rules.json', json.dumps(rules).encode(), '1 2\n')
    sid = submission_service.save_submission(student, assignment, 'addition.c',
        b'int main(void) {return 0;}')['submission_id']
    assert report_service.get_student_evaluation(student, sid) is None
    with factory() as session:
        session.add(FirstPassResult(submission_id=sid, status='ready',
                                    report_json=json.dumps(saved_report())))
        session.commit()

    data = report_service.get_student_evaluation(student, sid)
    assert data['overall_score'] == pytest.approx(70.0)
    assert data['filename'] == 'addition.c'
    assert data['failures'][0]['expected'] == '9\n'
    assert len(data['failures']) == 1
    txt = report_service.render_txt_report(data).decode('utf-8')
    assert '70.0 / 100' in txt and '4 5' in txt and '9' in txt
    assert 'USEFULNESS' in txt and 'LIMITATIONS' in txt
    assert 'SECRET_PEER' not in txt and 'private_peer.c' not in txt
    pdf = report_service.render_pdf_report(data)
    assert pdf.startswith(b'%PDF')
    import io
    reader = PdfReader(io.BytesIO(pdf))
    content = '\n'.join(p.extract_text() for p in reader.pages)
    assert 'addition.c' in content and 'Correctness' in content and 'Failed test examples' in content
    assert 'SECRET_PEER' not in content and 'private_peer.c' not in content
    with pytest.raises(PermissionError):
        report_service.get_student_evaluation(other, sid)
    with pytest.raises(PermissionError):
        report_service.get_student_evaluation(teacher, sid)
    with pytest.raises(ValueError):
        report_service.get_student_evaluation(student, sid + 99)


def test_na_metrics_do_not_prevent_provisional_grade():
    meta = {'submission_id': 1, 'student': 'Test', 'filename': 'x.c',
            'assignment': 'P', 'evaluated_at': datetime.now(timezone.utc)}
    report = saved_report()
    report['syntax_grading']['status'] = 'unavailable'
    data = report_service._build_evaluation(meta, report, {
        'metrics': [{'analyzer': 'io_tests', 'weight': 0.5},
                    {'analyzer': 'compiler_warnings', 'weight': 0.5}]})
    assert data['overall_score'] == 50.0 and data['provisional'] is True
    report['syntax_grading']['status'] = 'completed'
    data = report_service._build_evaluation(meta, report, {
        'metrics': [{'analyzer': 'io_tests', 'weight': 0.5},
                    {'analyzer': 'complexity_estimator', 'weight': 0.5}]})
    assert data['overall_score'] == 50.0 and data['provisional'] is True
    assert all(m['analyzer'] != 'complexity_estimator' for m in data['metrics'])
