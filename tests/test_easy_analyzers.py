"""Easy analyzer checks (compiler is mocked; never compile student C on host)."""
import json
from datetime import datetime, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from analyzers.syntax import evaluate_syntax
from analyzers.structure import evaluate_structure
from analyzers.modularity import evaluate_modularity
from analyzers.source_metrics import analyze_functions
from database.db import Base
from database.orm_models import EasyAnalyzerResult, FirstPassResult
from services import (authentication, assignment_service, student_service, submission_service,
                      easy_analyzer_service, storage_utils)
from services.dates import LOCAL_ZONE
from services.rules_service import example_rules_bytes


FAKE_FUNCTIONS = {
    'status': 'ok', 'functions': [
        {'name': 'main', 'cyclomatic_complexity': 2, 'length': 12, 'start_line': 2, 'end_line': 14},
        {'name': 'helper', 'cyclomatic_complexity': 1, 'length': 5, 'start_line': 15, 'end_line': 20},
    ]
}


def test_syntax_warning_scores_and_failed_compile():
    report = {'status': 'passed', 'warning_count': 2, 'diagnostics': 'warning: unused\nwarning: shadowed'}
    assert evaluate_syntax(report, {'penalty_per_warning': 0.1})['score'] == 0.8
    assert evaluate_syntax(report, {'flags': '-Wall -Wextra -Werror -std=c11'})['score'] == 0
    assert evaluate_syntax({'status': 'failed', 'diagnostics': 'error: expected ;'})['score'] == 0
    assert evaluate_syntax({'status': 'unavailable'})['score'] is None
    assert evaluate_syntax(report, {'flags': '-DLOCAL_TOKEN=5'})['score'] is None


def test_source_metrics_scoring():
    assert evaluate_structure(FAKE_FUNCTIONS, {'max_cyclomatic': 8, 'max_function_length': 40})['score'] == 1
    assert evaluate_modularity(FAKE_FUNCTIONS, {'expect_helpers': True, 'min_functions': 2})['score'] == 1
    solo = {'status': 'ok', 'functions': [FAKE_FUNCTIONS['functions'][0]]}
    assert evaluate_modularity(solo, {'expect_helpers': True, 'min_functions': 2})['score'] == 0.5
    assert evaluate_modularity(solo, {'expect_helpers': False, 'min_functions': 1})['score'] == 1
    long = {'status': 'ok', 'functions': [dict(FAKE_FUNCTIONS['functions'][0],
                                            cyclomatic_complexity=16, length=80)]}
    assert evaluate_structure(long, {'max_cyclomatic': 8, 'max_function_length': 40})['score'] == 0.5
    assert evaluate_structure({'status': 'unavailable', 'message': 'No Lizard'})['score'] is None
    with pytest.raises(ValueError):
        evaluate_structure(FAKE_FUNCTIONS, {'max_cyclomatic': 0})


def test_lizard_metrics_adapter(monkeypatch):
    import sys
    fake = SimpleNamespace(analyze_file=SimpleNamespace(analyze_source_code=lambda name, src:
        SimpleNamespace(function_list=[SimpleNamespace(name='main', cyclomatic_complexity=1,
                                                      length=7, start_line=1, end_line=7)])))
    monkeypatch.setitem(sys.modules, 'lizard', fake)
    info = analyze_functions('int main(void) {return 0;}')
    assert info['status'] == 'ok' and info['functions'][0]['name'] == 'main'


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "easy.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (authentication, assignment_service, student_service,
                   submission_service, easy_analyzer_service):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')
    tid = authentication.create_user('easy-teacher', 'teacher-pass-123', 'teacher')
    sid = student_service.create_student(tid, 'easy-student', 'student-pass-123', 'Student One')
    aid = assignment_service.create_assignment(
        tid, 'C Functions', 'Demo', datetime.now(LOCAL_ZONE) + timedelta(days=1),
        'problem.txt', b'demo assignment', 'rules.json', example_rules_bytes(), '1 2\n')
    subid = submission_service.save_submission(sid, aid, 'student.c',
        b'int main(void) {return 0;}')['submission_id']
    return tid, sid, subid, factory


def test_teacher_only_persistence_and_no_docker(workspace, monkeypatch):
    teacher, student, submission, factory = workspace
    monkeypatch.setattr(easy_analyzer_service, 'analyze_functions', lambda source: FAKE_FUNCTIONS)
    # Simulate Docker missing: the source-only metrics must still run.
    def offline(*args, **kwargs):
        from hashlib import sha256
        from database.orm_models import Submission, Assignment
        with factory() as sess:
            sub = sess.get(Submission, submission)
            assignment = sess.get(Assignment, sub.assignment_id)
            return {'status': 'unavailable', 'source_hash': sub.source_hash,
                    'rules_hash': assignment.rules_hash,
                    'compiler': {'status': 'unavailable'}, 'smoke': {'status': 'skipped'}}
    monkeypatch.setattr(easy_analyzer_service, 'evaluate_source', offline)
    result = easy_analyzer_service.run_easy_analyzers(teacher, submission)
    assert result['status'] == 'partial'
    assert len(result['metrics']) == 3
    assert result['metrics'][0]['score'] is None
    assert result['metrics'][1]['score'] == 1
    assert result['metrics'][2]['score'] == 1
    assert result['earned_points'] == 20
    with pytest.raises(PermissionError):
        easy_analyzer_service.get_easy_analyzers(student, submission)
    with pytest.raises(PermissionError):
        easy_analyzer_service.run_easy_analyzers(student, submission)
    other_teacher = authentication.create_user('easy-other', 'teacher-pass-123', 'teacher')
    with pytest.raises(PermissionError):
        easy_analyzer_service.get_easy_analyzers(other_teacher, submission)
    assert not easy_analyzer_service.get_easy_analyzers(teacher, submission)['stale']
    with factory() as session:
        assert session.query(EasyAnalyzerResult).count() == 1
        assert session.query(FirstPassResult).count() == 1
    submission_service.delete_submission(teacher, submission)
    with factory() as session:
        assert session.query(EasyAnalyzerResult).count() == 0
        assert session.query(FirstPassResult).count() == 0


def test_unchanged_first_pass_reused(workspace, monkeypatch):
    teacher, student, subid, factory = workspace
    from database.orm_models import Submission, Assignment
    monkeypatch.setattr(easy_analyzer_service, 'analyze_functions', lambda source: FAKE_FUNCTIONS)
    with factory() as session:
        sub = session.get(Submission, subid)
        a = session.get(Assignment, sub.assignment_id)
        first = {'status': 'ready', 'source_hash': sub.source_hash, 'rules_hash': a.rules_hash,
                 'compiler': {'status': 'passed', 'warning_count': 1, 'diagnostics': 'warning: unused'},
                 'smoke': {'status': 'passed'}}
        session.add(FirstPassResult(submission_id=subid, status='ready', report_json=json.dumps(first)))
        session.commit()
    monkeypatch.setattr(easy_analyzer_service, 'evaluate_source', lambda *a, **kw:
                        (_ for _ in ()).throw(AssertionError('Unnecessary compile')))
    report = easy_analyzer_service.run_easy_analyzers(teacher, subid)
    assert report['status'] == 'complete'
    assert report['metrics'][0]['score'] == 0.9
    assert report['earned_points'] == 29


def test_adaptive_nloc_and_modularity_policy():
    from analyzers.source_metrics import _without_c_comments
    src = 'int main(void){\n // a comment\n printf("// stays"); /* block */\n return 0;\n}\n'
    filtered = _without_c_comments(src)
    assert 'a comment' not in filtered
    assert '"// stays"' in filtered
    solo = {'status': 'ok', 'functions': [dict(FAKE_FUNCTIONS['functions'][0], nloc=8)]}
    assert evaluate_modularity(solo, {'mode': 'auto'})['score'] == 1.0
    assert evaluate_modularity(solo, {'mode': 'required', 'min_functions': 2})['score'] == 0.5
    assert evaluate_modularity(solo, {'mode': 'disabled'})['score'] is None
    long = {'status': 'ok', 'functions': [dict(FAKE_FUNCTIONS['functions'][0], nloc=80, cyclomatic_complexity=16)]}
    assert evaluate_structure(long, {'max_cyclomatic': 8, 'max_function_nloc': 40})['score'] == .5
    assert 'consider splitting' in evaluate_modularity(long, {'mode': 'auto', 'suggest_if_ccn_over': 10})['feedback']
