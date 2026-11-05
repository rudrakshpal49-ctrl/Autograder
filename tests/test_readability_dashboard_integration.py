"""Readability integration with stored reports and instructor metric rules."""
from datetime import datetime, timedelta

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from agent.analyzers import llm_review
from database.db import Base
from services import (authentication, assignment_service, student_service,
                      submission_service, first_pass_service, storage_utils)
from services.dates import LOCAL_ZONE
from services.first_pass_service import attach_readability_grading


def fake_report(smoke='timeout'):
    return {'status': 'blocked' if smoke == 'timeout' else 'ready',
            'compiler': {'status': 'passed', 'warning_count': 0, 'diagnostics': ''},
            'smoke': {'status': smoke}, 'static_sanity': {'status': 'passed'}}


def test_default_preview_when_no_metrics(monkeypatch):
    monkeypatch.setattr(llm_review, '_request_ollama', lambda *args: {'score': 10, 'strengths': ['Clear structure.'], 'issues': []})
    report = fake_report()
    attach_readability_grading(report, {'time_limit_s': 1, 'memory_limit_mb': 64},
                               b'int main(void){return 0;}')
    assert report['readability_grading']['status'] == 'completed'
    assert report['readability_grading']['rule_source'] == 'default_preview'
    assert report['readability_grading']['weight'] is None
    assert report['readability_grading']['score'] == 1.0
    assert report['readability_grading']['evidence']['scoring_method'] == 'llm'
    assert report['readability_grading']['evidence']['ai']['status'] == 'completed'


def test_custom_metric_parameters_and_skipped_if_disabled():
    report = fake_report()
    attach_readability_grading(report, {'metrics': [
        {'name': 'syntax', 'analyzer': 'compiler_warnings'},
    ]}, b'int main(){}')
    assert report['readability_grading']['status'] == 'not_configured'

    report = fake_report()
    attach_readability_grading(report, {'metrics': [
        {'name': 'readability', 'analyzer': 'llm_review', 'weight': 0.25,
         'params': {'require_comments': True, 'use_llm': False}},
    ]}, b'int main(){}')
    assert report['readability_grading']['status'] == 'completed'
    assert report['readability_grading']['score'] == 0.95
    assert report['readability_grading']['weight'] == 0.25


def test_teacher_can_view_persisted_readability_after_timeout_and_cache(monkeypatch, tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "readability.db"}')
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
        teacher, 'Readable code', 'Use readable C',
        datetime.now(LOCAL_ZONE) + timedelta(days=2),
        'task.txt', b'Write C', 'rubric.json',
        b'{"time_limit_s":1,"memory_limit_mb":64,"metrics":['
        b'{"name":"readability","analyzer":"llm_review","weight":1.0,'
        b'"params":{"use_llm":true}}]}', '1 2\n')
    submission = submission_service.save_submission(
        student, assignment, 'solution.c', b'int main(void){return 0;}')['submission_id']
    monkeypatch.setattr(first_pass_service, 'evaluate_source', lambda *args, **kwargs: fake_report())
    calls = []
    monkeypatch.setattr(llm_review, '_request_ollama',
                        lambda src, model, require_comments=False: calls.append(src) or {'score': 10, 'strengths': [], 'issues': []})

    assert first_pass_service.run_first_pass(student, submission) == {'submission_id': submission}
    saved = first_pass_service.get_first_pass(teacher, submission)
    assert saved['readability_grading']['status'] == 'completed'
    assert saved['readability_grading']['score'] == 1.0
    assert saved['readability_grading']['scoring_method'] == 'llm'
    assert saved['readability_grading']['evidence']['ai']['suggestions'] == []
    assert len(calls) == 1

    assert first_pass_service.run_first_pass(student, submission) == {'submission_id': submission}
    saved_again = first_pass_service.get_first_pass(teacher, submission)
    assert saved_again['readability_grading']['evidence']['ai']['cached'] is True
    assert len(calls) == 1


def test_teacher_gets_provisional_result_if_model_unavailable(monkeypatch):
    monkeypatch.setattr(llm_review, '_request_ollama', lambda *args: (_ for _ in ()).throw(OSError()))
    report = fake_report()
    attach_readability_grading(report, {'metrics': [
        {'name': 'readability', 'analyzer': 'llm_review', 'weight': 1.0, 'params': {}}
    ]}, b'int main(void){return 0;}')
    assert report['readability_grading']['status'] == 'completed'
    assert report['readability_grading']['score'] == 1.0
    assert report['readability_grading']['evidence']['scoring_method'] == 'python_fallback'
