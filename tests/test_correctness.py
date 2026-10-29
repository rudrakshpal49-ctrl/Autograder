"""No compiler executed on host: Docker calls mocked for offline CI."""
import json
from datetime import datetime, timedelta
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from database.db import Base
from database.orm_models import CorrectnessResult
from services import (authentication, assignment_service, student_service, submission_service,
                      testcase_service, correctness_service, first_pass_service, storage_utils)
from services.dates import LOCAL_ZONE
from services.rules_service import example_rules_bytes
from analyzers.correctness import evaluate_correctness, normalize_output
from agent import sandbox


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "grader.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (authentication, assignment_service, student_service, submission_service,
                   testcase_service, correctness_service, first_pass_service):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')
    teacher = authentication.create_user('teacher-x', 'secure-password-123', 'teacher')
    student = student_service.create_student(teacher, 'student-x', 'secure-password-456', 'Student X')
    aid = assignment_service.create_assignment(teacher, 'Addition', 'Read 2 integers and print their sum.',
        datetime.now(LOCAL_ZONE) + timedelta(days=5), 'problem.txt',
        b'Add two integers using signed int within valid range.', 'rules.json', example_rules_bytes(), '1 2\n')
    sid = submission_service.save_submission(student, aid, 'student.c', b'int main(void) {return 0;}\n')['submission_id']
    testcase_service.save_reference(teacher, aid, 'correct.c', b'int main(void){return 0;}\n')
    return teacher, student, aid, sid, factory


def fake_reference(source, inputs, **options):
    outputs = []
    for sample in inputs:
        numbers = list(map(int, sample.split()))
        outputs.append({'status': 'passed', 'stdout': f'{sum(numbers)}\n', 'stderr': '', 'wall_time_ms': 3})
    return {'status': 'ok', 'image_id': 'sha256:ref', 'results': outputs}


def fake_student(source, inputs, **options):
    outputs = [{'status': 'passed', 'stdout': '3\n' if i == 0 else 'WRONG\n',
                'stderr': '', 'wall_time_ms': 4} for i in range(len(inputs))]
    return {'status': 'ok', 'image_id': 'sha256:test', 'results': outputs}


def test_reference_approval_and_student_teacher_visibility(workspace, monkeypatch):
    teacher, student, aid, sid, factory = workspace
    another_teacher = authentication.create_user('another-x', 'strong-password-123', 'teacher')
    inputs = [{'input': '1 2\n', 'category': 'normal'},
              {'input': '-1 0\n', 'category': 'negative'}]
    monkeypatch.setattr(testcase_service, 'run_c_tests', fake_reference)
    prepared = testcase_service.prepare_testset(teacher, aid, inputs)
    assert prepared['cases'][0]['expected'] == '3\n'
    assert testcase_service.approve_testset(teacher, aid, prepared)
    assert testcase_service.get_testset(teacher, aid)['tests_approved']
    with pytest.raises(PermissionError):
        testcase_service.get_testset(student, aid)
    with pytest.raises(PermissionError):
        testcase_service.get_testset(another_teacher, aid)
    monkeypatch.setattr(correctness_service, 'run_c_tests', fake_student)
    assert correctness_service.run_correctness(student, sid) == {'submission_id': sid}
    with pytest.raises(PermissionError):
        correctness_service.get_correctness(student, sid)
    report = correctness_service.get_correctness(teacher, sid)
    assert report['score'] == 0.5
    assert report['passed'] == 1 and report['total'] == 2
    assert report['results'][1]['input'] == '-1 0\n'
    assert report['results'][1]['expected'] == '-1\n'
    assert report['results'][1]['status'] == 'wrong_answer'
    assert not report['stale']
    with pytest.raises(PermissionError):
        correctness_service.get_correctness(another_teacher, sid)
    submission_service.delete_submission(teacher, sid)
    with factory() as session:
        assert session.query(CorrectnessResult).count() == 0


def test_changed_reference_invalidates_tests_and_previous_reports(workspace, monkeypatch):
    teacher, student, aid, sid, _ = workspace
    monkeypatch.setattr(testcase_service, 'run_c_tests', fake_reference)
    cases = testcase_service.prepare_testset(teacher, aid, [{'input': '1 2'}])
    testcase_service.approve_testset(teacher, aid, cases)
    monkeypatch.setattr(correctness_service, 'run_c_tests', fake_student)
    correctness_service.run_correctness(student, sid)
    testcase_service.save_reference(teacher, aid, 'new.c', b'int main(){puts("revised");}')
    assert testcase_service.get_testset(teacher, aid)['tests_approved'] is False
    assert correctness_service.get_correctness(teacher, sid)['stale'] is True
    assert correctness_service.run_correctness(student, sid) == {'submission_id': sid}


def test_validation_and_output_policy():
    assert normalize_output('hi  \r\n') == normalize_output('hi\n')
    assert normalize_output('a b\n') != normalize_output('ab\n')
    with pytest.raises(ValueError, match='Duplicate'):
        testcase_service.validate_input_cases([{'input': 'same'}, {'input': 'same'}])
    with pytest.raises(ValueError):
        testcase_service.validate_input_cases([{'input': 'x' * 8193}])
    tests = [{'input': '5 7', 'expected': '12\n', 'category': 'normal'}]
    assert evaluate_correctness(tests, {'status': 'unavailable', 'message': 'Docker missing'})['score'] is None
    data = {'status': 'ok', 'results': [{'status': 'passed', 'stdout': '12 \r\n', 'stderr': ''}]}
    assert evaluate_correctness(tests, data)['score'] == 1.0


def test_docker_missing_does_not_invoke_host_compiler(monkeypatch):
    monkeypatch.setattr(sandbox, 'check_environment', lambda: (False, 'Docker missing', None))
    data = sandbox.run_c_tests(b'int main(void){return 0;}', ['1 2\n'])
    assert data['status'] == 'unavailable'
    assert data['results'] == []


def test_generated_inputs_parser(monkeypatch):
    from agent import test_generator
    # Test public manual-format validation while Ollama is optional.
    cases = testcase_service.validate_input_cases(json.dumps({'cases': [{'input': '1 2\n', 'category': 'normal'}]}))
    assert cases == [{'input': '1 2\n', 'category': 'normal'}]
    assert test_generator.extract_problem_text('problem.txt', b'Hello world') == 'Hello world'
