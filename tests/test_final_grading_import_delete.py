"""Integration regression checks for final scoring, JSON bank and assignment deletion."""
from datetime import datetime, timedelta
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.orm_models import Assignment, Submission, FirstPassResult, GradingCase
from services import (authentication, assignment_service, student_service,
                      submission_service, storage_utils, testset_service)
from services.final_grade_service import calculate_final_grade
from services.dates import LOCAL_ZONE


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "fixture.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (authentication, assignment_service, student_service,
                   submission_service, testset_service):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')
    teacher = authentication.create_user('rubric_teacher', 'password123', 'teacher')
    other_teacher = authentication.create_user('rubric_other', 'password123', 'teacher')
    student = student_service.create_student(teacher, 'rubric_student', 'password123', 'Student')
    deadline = datetime.now(LOCAL_ZONE) + timedelta(days=2)
    rules = b'{"time_limit_s":1,"memory_limit_mb":64,"metrics":[{"name":"syntax","analyzer":"compiler_warnings","weight":1.0}]}'
    def create(title):
        return assignment_service.create_assignment(teacher, title, 'Desc', deadline,
            'task.txt', b'Test assignment', 'rubric.json', rules)
    return teacher, other_teacher, student, factory, tmp_path, create


def test_final_grade_weights_and_zero_scores_are_valid():
    rules = {'metrics': [
        {'name': 'Syntax', 'analyzer': 'compiler_warnings', 'weight': .25},
        {'name': 'Correctness', 'analyzer': 'io_tests', 'weight': .75},
    ]}
    report = {'syntax_grading': {'status': 'completed', 'score': 1.0},
              'correctness_grading': {'status': 'completed', 'score': 0.0}}
    result = calculate_final_grade(report, rules)
    assert result['status'] == 'complete'
    assert result['score'] == 25.0
    assert [x['contribution'] for x in result['breakdown']] == [25.0, 0.0]
    report['correctness_grading']['status'] = 'unavailable'
    assert calculate_final_grade(report, rules)['score'] == 100.0
    assert calculate_final_grade(report, rules)['status'] == 'provisional'
    report['correctness_grading']['status'] = 'completed'
    assert calculate_final_grade(report, {'metrics': rules['metrics'][:1]})['score'] is None
    dupes = {'metrics': rules['metrics'] + [rules['metrics'][0]]}
    assert calculate_final_grade(report, dupes)['score'] is None
    assert calculate_final_grade(report, {'metrics': []})['status'] == 'not_configured'


def test_final_grade_not_affected_by_unconfigured_preview():
    rules = {'metrics': [{'analyzer': 'io_tests', 'weight': 1.0}]}
    report = {'correctness_grading': {'status': 'completed', 'score': .85},
              'syntax_grading': {'status': 'completed', 'score': 0.0}}
    assert calculate_final_grade(report, rules)['score'] == 85.0


def test_import_json_approved_duplicate_detection_and_invalidation(workspace):
    teacher, other_teacher, student, factory, base, create = workspace
    aid = create('Batch import')
    sid = submission_service.save_submission(student, aid, 'ok.c', b'int main(){return 0;}')['submission_id']
    with factory() as session:
        session.add(FirstPassResult(submission_id=sid, status='ready', report_json='{}'))
        session.commit()
    payload = json.dumps({'cases': [
        {'category': 'normal', 'stdin': '1 2\n', 'expected_output': '3\n', 'title': 'simple'},
        {'category': 'edge', 'stdin': '0 0\n', 'expected_output': '0\n', 'edge_type': 'zero_values'},
    ]}).encode()
    with pytest.raises(PermissionError):
        testset_service.import_teacher_cases_json(other_teacher, aid, payload)
    result = testset_service.import_teacher_cases_json(teacher, aid, payload)
    assert result == {'added': 2, 'skipped_duplicates': 0, 'total_in_file': 2}
    cases = testset_service.list_cases(teacher, aid)
    assert len(cases) == 2 and all(case['approved'] for case in cases)
    assert [case['edge_type'] for case in cases] == [None, 'zero_values']
    with factory() as session:
        assert session.scalar(select(FirstPassResult).where(FirstPassResult.submission_id == sid)) is None
    assert testset_service.import_teacher_cases_json(teacher, aid, payload)['skipped_duplicates'] == 2
    assert len(testset_service.list_cases(teacher, aid)) == 2


def test_json_parser_rejects_partial_bad_and_overlarge_files():
    read = testset_service.parse_teacher_cases_json
    with pytest.raises(ValueError, match='JSON'):
        read(b'not json')
    with pytest.raises(ValueError, match='256 KiB'):
        read(b'x' * (256 * 1024 + 1))
    with pytest.raises(ValueError, match='duplicate'):
        read(json.dumps([{'stdin': '1', 'expected_output': '2'},
                         {'stdin': '1\n', 'expected_output': '2'}]).encode())
    with pytest.raises(ValueError, match='Case 2'):
        read(json.dumps([{'stdin': '1', 'expected_output': '2'},
                         {'stdin': '2', 'expected_output': 3}]).encode())
    assert read(b'\xef\xbb\xbf[{"stdin":"1","expected_output":"2"}]')[0]['category'] == 'normal'


def test_delete_assignment_cascades_and_preserves_other_assignments(workspace):
    teacher, outsider, student, factory, base, create = workspace
    first, second = create('Remove me'), create('Keep me')
    sid = submission_service.save_submission(student, first, 'solution.c', b'int main(){return 0;}')['submission_id']
    keep = submission_service.save_submission(student, second, 'keep.c', b'int main(){return 0;}')['submission_id']
    testset_service.add_teacher_case(teacher, first, 'normal', '1\n', '1\n', 'test')
    with factory() as session:
        session.add(FirstPassResult(submission_id=sid, status='ready', report_json='{}'))
        session.commit()
    with factory() as session:
        row = session.get(Submission, sid)
        paths = [row.source_path, session.get(Assignment, first).document_path,
                 session.get(Assignment, first).rules_path]
    with pytest.raises(PermissionError):
        assignment_service.delete_assignment(outsider, first)
    with pytest.raises(PermissionError):
        assignment_service.delete_assignment(student, first)
    result = assignment_service.delete_assignment(teacher, first)
    assert result['submissions_deleted'] == 1 and result['test_cases_deleted'] == 1
    assert result['file_cleanup_warnings'] == []
    with factory() as session:
        assert session.get(Assignment, first) is None
        assert session.get(Assignment, second) is not None
        assert session.get(Submission, sid) is None
        assert session.get(Submission, keep) is not None
        assert session.scalars(select(FirstPassResult)).all() == []
        assert session.scalars(select(GradingCase)).all() == []
    for raw in paths:
        assert not (base / raw).exists()
    assert len(submission_service.get_student_submissions(student)) == 1
    with pytest.raises(PermissionError):
        assignment_service.delete_assignment(teacher, first)


def test_ui_includes_final_grade_json_import_and_hide_red_tab_accent():
    source = Path(__file__).resolve().parents[1].joinpath('app.py').read_text(encoding='utf-8')
    assert 'FINAL WEIGHTED GRADE' in source
    assert 'import_teacher_cases_json' in source
    assert 'Delete assignment permanently' in source
    assert '[data-baseweb="tab-highlight"]' in source
    assert 'escape(str(user[' in source


def test_first_pass_persists_final_grade_and_rules_update_invalidates_it(workspace, monkeypatch):
    from services import first_pass_service
    teacher, _, student, factory, _, create = workspace
    monkeypatch.setattr(first_pass_service, 'SessionLocal', factory)
    aid = create('Final score')
    sid = submission_service.save_submission(student, aid, 'check.c',
        b'int main(){return 0;}')['submission_id']
    monkeypatch.setattr(first_pass_service, 'evaluate_source', lambda *args, **kwargs: {
        'status': 'ready', 'compiler': {'status': 'passed'},
        'smoke': {'status': 'passed'}, 'static_sanity': {'status': 'passed'},
        'rules_hash': kwargs.get('rules_hash'),
    })
    monkeypatch.setattr(first_pass_service, 'attach_syntax_grading', lambda report, rules:
        report.update(syntax_grading={'status': 'completed', 'score': .8, 'feedback': 'Test'}))
    report = first_pass_service.run_first_pass(teacher, sid)
    assert report['final_grade']['score'] == 80.0
    assert report['final_grade']['status'] == 'complete'
    assert first_pass_service.get_first_pass(teacher, sid)['final_grade']['score'] == 80.0
    with factory() as session:
        stored = session.scalar(select(FirstPassResult).where(FirstPassResult.submission_id == sid))
        assert json.loads(stored.report_json)['final_grade']['score'] == 80.0
    changed_rules = b'{"time_limit_s":1,"memory_limit_mb":64,"metrics":[' \
                    b'{"name":"correctness","analyzer":"io_tests","weight":1.0}]}'
    assignment_service.update_assignment(teacher, aid, 'Final score', 'New rubric',
        datetime.now(LOCAL_ZONE) + timedelta(days=2),
        rules_name='updated.json', rules_bytes=changed_rules)
    assert first_pass_service.get_first_pass(teacher, sid) is None


def test_teacher_can_delete_only_own_assignment_even_with_matching_title(workspace):
    teacher, other, student, factory, base, create = workspace
    assignment = create('Same name')
    # A separate teacher's assignment must not be affected by deletion.
    deadline = datetime.now(LOCAL_ZONE) + timedelta(days=3)
    foreign = assignment_service.create_assignment(other, 'Same name', 'X', deadline,
         'foreign.txt', b'Other task')
    assignment_service.delete_assignment(teacher, assignment)
    with factory() as session:
        assert session.get(Assignment, foreign) is not None
