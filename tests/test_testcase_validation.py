"""Regression tests for PDF-driven input validation, retries and output checks."""
import pytest

from agent import test_generator
from agent.test_validation import review_case


ARRAY_PDF_TEXT = '''
Array Assignment: Find the Maximum
Write a C program that reads an array of integers and prints the largest element in the array.
Input format
The first line contains one integer n, the number of elements. The second line contains exactly n
space-separated integers. Line breaks and spaces are interchangeable as separators between these integer tokens.
Output format
Print exactly one integer: the maximum element in the array, followed by a newline.
Constraints
1 <= n <= 100. Every array element is an integer in the inclusive range [-1000, 1000].
Sample input
5
3 9 -2 7 1
Sample output
9
'''


@pytest.mark.parametrize('bad_input', [
    '5 7\n3 9 -2 7 1\n',
    '3 2\n1 2 3\n',
    '100 1000\n999 1000 1000\n',
    '1 1\n1\n',
    '100 100\n1000 1000 1000 1000 1000\n',
    '2\n-1001 0\n',
    '101\n' + ('1 ' * 101),
])
def test_invalid_array_inputs_are_rejected(bad_input):
    assert review_case(ARRAY_PDF_TEXT, bad_input).status == 'invalid'


def test_valid_array_input_corrects_llm_expected_output():
    result = review_case(ARRAY_PDF_TEXT, '5\n3 9 -2 7 1\n', '10\n')
    assert result.status == 'valid'
    assert result.verified_output == '9\n'


def test_bad_llm_array_case_retried_and_repaired(monkeypatch):
    calls = []
    def fake_model(prompt, model=None, *, schema=None):
        calls.append(prompt)
        if len(calls) == 1:
            return {'cases': [
                {'input': '5 7\n3 9 -2 7 1\n', 'expected': '9\n', 'category': 'normal'},
                {'input': '100 1000\n999 1000\n', 'expected': '1000\n', 'category': 'normal'},
            ]}
        return {'cases': [
            {'input': '5\n3 9 -2 7 1\n', 'expected': '500\n', 'category': 'normal'},
        ]}
    monkeypatch.setattr(test_generator, '_ask_ollama', fake_model)
    result = test_generator.generate_inputs(ARRAY_PDF_TEXT, num_cases=3)
    assert len(calls) == 2  # one initial request and one bounded repair
    assert len(result) == 1
    assert result[0]['input'] == '5\n3 9 -2 7 1\n'
    assert result[0]['expected'] == '9\n'
    assert result[0]['expected_source'] == 'checker'
    assert 'n=5 but 6 array elements' in calls[1]


def test_unknown_problem_is_marked_for_manual_review(monkeypatch):
    monkeypatch.setattr(test_generator, '_ask_ollama', lambda *args, **kwargs: {'cases': [
        {'input': '4 2 1 3\n', 'expected': 'yes\n', 'category': 'normal'}]})
    result = test_generator.generate_inputs('Calculate special properties of directed graphs.', num_cases=3)
    assert result[0]['input_validation'] == 'requires_teacher_review'
    assert result[0]['expected_source'] == 'llm'


def test_string_vowel_oracle_only_when_explicitly_specified():
    spec = ('Write a C program that reads one complete line of text and counts English vowels '
            'in uppercase or lowercase. Count every occurrence of a vowel.\n'
            'Input format: The input consists of exactly one line of text.')
    result = review_case(spec, 'Hello World\n', '99\n')
    assert result.status == 'valid'
    assert result.verified_output == '3\n'
    assert review_case(spec, 'line1\nline2\n').status == 'invalid'


def test_approval_cannot_accept_wrong_array_counts(setup_array_project):
    service, teacher, assignment_id = setup_array_project
    with pytest.raises(ValueError, match='n=5 but 6'):
        service.prepare_testset(teacher, assignment_id,
                                [{'input': '5 7\n3 9 -2 7 1\n', 'expected': '9\n'}])


def test_prepare_uses_python_checker_when_llm_prediction_is_wrong(setup_array_project):
    service, teacher, aid = setup_array_project
    prepared = service.prepare_testset(teacher, aid, [{
        'input': '5\n3 9 -2 7 1\n', 'expected': '123456\n',
        'expected_source': 'llm', 'category': 'normal',
    }])
    assert prepared['cases'][0]['expected'] == '9\n'
    assert prepared['cases'][0]['expected_source'] == 'checker'
    assert service.approve_testset(teacher, aid, prepared)


def test_approval_rechecks_previewed_input(setup_array_project):
    service, teacher, aid = setup_array_project
    prepared = service.prepare_testset(teacher, aid, [{
        'input': '5\n3 9 -2 7 1\n', 'expected': '9\n',
        'expected_source': 'llm', 'category': 'normal',
    }])
    prepared['cases'][0]['input'] = '5 7\n3 9 -2 7 1\n'
    with pytest.raises(ValueError, match='n=5 but 6'):
        service.approve_testset(teacher, aid, prepared)


@pytest.fixture
def setup_array_project(tmp_path, monkeypatch):
    from datetime import datetime, timedelta
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from database.db import Base
    from services import (authentication, testcase_service, assignment_service,
                          storage_utils)
    from services.dates import LOCAL_ZONE
    from services.rules_service import example_rules_bytes
    engine = create_engine(f'sqlite:///{tmp_path / "test.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (authentication, testcase_service, assignment_service):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')
    teacher = authentication.create_user('array_teacher', 'secure-password-123', 'teacher')
    assignment_id = assignment_service.create_assignment(
        teacher, 'Maximum in Array', '', datetime.now(LOCAL_ZONE) + timedelta(days=1),
        'assignment.txt', ARRAY_PDF_TEXT.encode(), 'rules.json', example_rules_bytes(), '')
    return testcase_service, teacher, assignment_id
