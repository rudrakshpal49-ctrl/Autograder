"""PDF-driven generation and optional teacher tests; no Docker or live Ollama needed."""
import io
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from services import (authentication, assignment_service, student_service,
                      submission_service, first_pass_service, testcase_service,
                      correctness_service, storage_utils)
from services.dates import LOCAL_ZONE
from services.rules_service import example_rules_bytes
from agent.test_generator import extract_problem_text


def make_pdf():
    # Optional test-only dependency; the app itself uses pypdf, not reportlab.
    canvas_lib = pytest.importorskip('reportlab.pdfgen.canvas')
    memory = io.BytesIO()
    pdf = canvas_lib.Canvas(memory)
    pdf.drawString(50, 780, 'Read two integers a and b.')
    pdf.drawString(50, 755, 'Print their sum and a newline.')
    pdf.drawString(50, 730, 'Both numbers are between -1000 and 1000.')
    pdf.showPage()
    pdf.save()
    return memory.getvalue()


@pytest.fixture
def setup_project(monkeypatch, tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "demo.db"}')
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    for module in (authentication, assignment_service, student_service,
                   submission_service, first_pass_service, testcase_service,
                   correctness_service):
        monkeypatch.setattr(module, 'SessionLocal', session_factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')
    teacher = authentication.create_user('pdf_teacher', 'secure-password-123', 'teacher')
    student = student_service.create_student(teacher, 'pdf_student', 'secure-password-456', 'Pdf Student')
    pdf = make_pdf()
    assignment_id = assignment_service.create_assignment(
        teacher, 'PDF addition', 'Use signed integers.',
        datetime.now(LOCAL_ZONE) + timedelta(days=3), 'assignment.pdf', pdf,
        'rubric.json', example_rules_bytes(), '')
    submission = submission_service.save_submission(
        student, assignment_id, 'submission.c', b'#include <stdio.h>\nint main(){return 0;}\n'
    )['submission_id']
    return teacher, student, assignment_id, submission, pdf


def test_pdf_is_primary_source_and_reference_is_optional(setup_project, monkeypatch):
    teacher, student, aid, sid, pdf = setup_project
    extracted = extract_problem_text('assignment.pdf', pdf)
    assert 'two integers' in extracted
    assert 'Print their sum' in extracted

    def fake_generate(problem_text, **kwargs):
        assert 'Read two integers' in problem_text
        assert 'Both numbers are between -1000 and 1000' in problem_text
        return [{'input': '5 7\n', 'expected': '12\n', 'category': 'normal', 'expected_source': 'llm'},
                {'input': '-4 1\n', 'expected': '-3\n', 'category': 'negative', 'expected_source': 'llm'}]

    monkeypatch.setattr(testcase_service, 'generate_inputs', fake_generate)
    generated = testcase_service.suggest_cases(teacher, aid)
    optional = testcase_service.parse_optional_cases(
        '[{"input":"0 0\\n","expected":"0\\n","category":"teacher"}]')
    merged = testcase_service.merge_cases(generated, optional)
    assert len(merged) == 3
    monkeypatch.setattr(testcase_service, 'run_c_tests',
                        lambda *_args, **_kw: pytest.fail('Reference must not run when none was uploaded'))
    prepared = testcase_service.prepare_testset(teacher, aid, merged)
    assert prepared['reference_hash'] is None
    assert prepared['cases'][0]['expected_source'] == 'llm'
    assert prepared['cases'][2]['expected_source'] == 'teacher'
    assert 'manually verified' in prepared['warnings'][0]
    testcase_service.approve_testset(teacher, aid, prepared)
    assert len(testcase_service.get_testset(teacher, aid)['cases']) == 3

    def mocked_student(source, inputs, **kwargs):
        assert len(inputs) == 3
        return {'status': 'ok', 'results': [
            {'status': 'passed', 'stdout': '12\n', 'stderr': '', 'wall_time_ms': 3},
            {'status': 'passed', 'stdout': '-3\n', 'stderr': '', 'wall_time_ms': 3},
            {'status': 'passed', 'stdout': 'WRONG\n', 'stderr': '', 'wall_time_ms': 3},
        ]}

    monkeypatch.setattr(correctness_service, 'run_c_tests', mocked_student)
    assert correctness_service.run_correctness(student, sid) == {'submission_id': sid}
    report = correctness_service.get_correctness(teacher, sid)
    assert report['score'] == pytest.approx(0.6667)
    assert report['results'][2]['expected'] == '0\n'
    with pytest.raises(PermissionError):
        correctness_service.get_correctness(student, sid)
    with pytest.raises(PermissionError):
        testcase_service.get_testset(student, aid)

    assignment_service.update_assignment(
        teacher, aid, 'PDF addition', 'Use signed integers, now input up to 9999.',
        datetime.now(LOCAL_ZONE) + timedelta(days=2), sample_input='5 7\n')
    assert not testcase_service.get_testset(teacher, aid)['tests_approved']
    assert correctness_service.get_correctness(teacher, sid)['stale']
    with pytest.raises(ValueError, match='changed'):
        testcase_service.approve_testset(teacher, aid, prepared)


def test_additional_teacher_input_without_output_uses_llm(setup_project, monkeypatch):
    teacher, _, aid, _, _ = setup_project
    monkeypatch.setattr(testcase_service, 'generate_expected_outputs',
                        lambda problem_text, cases: ['300\n'] if 'Print their sum' in problem_text else [])
    prepared = testcase_service.prepare_testset(teacher, aid, [{'input': '100 200\n', 'category': 'teacher'}])
    assert prepared['cases'][0]['expected'] == '300\n'
    assert prepared['cases'][0]['expected_source'] == 'llm'
    assert testcase_service.approve_testset(teacher, aid, prepared)


def test_optional_input_file_and_duplicate_handling():
    a = testcase_service.parse_optional_cases(filename='mytests.txt', file_data=b'1 2\n0 0\n')
    assert [v['input'] for v in a] == ['1 2\n', '0 0\n']
    merged = testcase_service.merge_cases(
        [{'input': '1 2\n', 'expected': 'wrong\n', 'category': 'normal', 'expected_source': 'llm'}],
        [{'input': '1 2\n', 'expected': '3\n', 'category': 'teacher'}])
    assert merged[0]['expected'] == '3\n'
    assert merged[0]['expected_source'] == 'teacher'
    with pytest.raises(ValueError):
        testcase_service.parse_optional_cases('[]', 'cases.json', b'[]')


def test_first_pass_picks_pdf_generated_smoke_input(setup_project, monkeypatch):
    teacher, student, aid, sid, _ = setup_project
    captured = []

    def fake_check(source, *, sample_input, **options):
        captured.append(sample_input)
        return {'status': 'ready', 'compiler': {'status': 'passed'},
                'smoke': {'status': 'passed'}, 'static_sanity': {'status': 'passed'},
                'source_hash': 'a', 'rules_hash': options['rules_hash']}

    monkeypatch.setattr(first_pass_service, 'evaluate_source', fake_check)
    first_pass_service.run_first_pass(student, sid)
    assert captured == [None]  # no approved tests yet; smoke is skipped
    testcase_service.approve_testset(teacher, aid, testcase_service.prepare_testset(
        teacher, aid, [{'input': '5 7\n', 'expected': '12\n', 'category': 'normal'}]))
    first_pass_service.run_first_pass(student, sid)
    assert captured[-1] == '5 7\n'


def test_ollama_response_contains_expected_output(monkeypatch):
    import json
    from agent import test_generator

    class FakeResponse:
        def __enter__(self):
            return self
        def __exit__(self, *_args):
            return False
        def read(self, size):
            return json.dumps({'response': json.dumps({'cases': [
                {'input': '5 7\n', 'expected': '12\n', 'category': 'normal'}
            ]})}).encode()

    monkeypatch.setattr(test_generator.urllib.request, 'urlopen',
                        lambda request, timeout: FakeResponse())
    generated = test_generator.generate_inputs('Read two integers, print sum.')
    assert generated == [{'input': '5 7\n', 'category': 'normal', 'expected': '12\n',
                          'expected_source': 'llm'}]
