"""Sample-guided Ollama generation and two counted-array validation."""
import pytest
from datetime import datetime, timedelta
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from services import authentication, assignment_service, storage_utils, testcase_service
from services.dates import LOCAL_ZONE
from services.rules_service import example_rules_bytes
from agent import test_generator
from agent.test_validation import review_case


TWO_ARRAY_PROBLEM = '''
Common Elements in Two Arrays
Given two arrays of integers A and B, print all distinct common elements
in the order of their first appearance in array A. Print None if none.
Input Format
1. The first line contains an integer n, the number of elements in array A.
2. The second line contains n space-separated integers: the elements of A.
3. The third line contains an integer m, the number of elements in array B.
4. The fourth line contains m space-separated integers: the elements of B.
Output Format
Print the common elements once each, separated by one space.
Constraints
1 <= n, m <= 100
-1000 <= A[i], B[i] <= 1000
'''
SAMPLE_IN = '6\n4 2 4 -1 7 2\n5\n2 -1 8 4 4\n'
SAMPLE_OUT = '4 2 -1\n'


@pytest.fixture
def demo(tmp_path, monkeypatch):
    engine = create_engine('sqlite:///' + str(tmp_path / 'case.db'))
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for module in (authentication, assignment_service, testcase_service):
        monkeypatch.setattr(module, 'SessionLocal', factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')
    teacher = authentication.create_user('example_teacher', 'test-password-123', 'teacher')
    student = authentication.create_user('example_student', 'test-password-123', 'student')
    aid = assignment_service.create_assignment(
        teacher, 'Common Elements', '', datetime.now(LOCAL_ZONE) + timedelta(days=2),
        'assignment.txt', TWO_ARRAY_PROBLEM.encode(), 'rules.json', example_rules_bytes(),
        sample_input=SAMPLE_IN, sample_output=SAMPLE_OUT)
    return teacher, student, aid, factory


def test_generation_prompt_uses_saved_stdin_and_stdout(demo, monkeypatch):
    teacher, student, aid, _ = demo
    seen = []

    def fake_llm(prompt, model=None, *, schema=None):
        seen.append(prompt)
        assert 'TEACHER SAMPLE STDIN' in prompt
        assert SAMPLE_IN in prompt
        assert 'TEACHER SAMPLE STDOUT' in prompt
        assert SAMPLE_OUT in prompt
        return {'cases': [
            {'input': SAMPLE_IN, 'expected': SAMPLE_OUT, 'category': 'copied'},
            {'input': '3\n1 2 3\n2\n2 4\n', 'expected': '2\n', 'category': 'normal'},
        ]}

    monkeypatch.setattr(test_generator, '_ask_ollama', fake_llm)
    cases = testcase_service.suggest_cases(teacher, aid)
    assert len(seen) == 1
    assert len(cases) == 1  # sample ignored as duplicate example
    assert cases[0]['input'] == '3\n1 2 3\n2\n2 4\n'
    assert cases[0]['expected_source'] == 'llm'
    assert assignment_service.get_all_assignments()[0].get('sample_output') is None
    with pytest.raises(PermissionError):
        testcase_service.suggest_cases(student, aid)


@pytest.mark.parametrize('input_text, status', [
    (SAMPLE_IN, 'valid'),
    ('3\n1 2 3\n2\n2 4\n', 'valid'),
    ('6\n4 2 4 -1 7 2\n5\n2 -1 8 4 4 9\n', 'invalid'),
    ('3\n1 2 3\n5\n2 4\n', 'invalid'),
    ('101\n', 'invalid'),
    ('1\n2000\n1\n2000\n', 'invalid'),
    ('3\n1 x 3\n2\n1 2\n', 'invalid'),
])
def test_two_counted_array_validation(input_text, status):
    assert review_case(TWO_ARRAY_PROBLEM, input_text).status == status


def test_two_array_format_is_checked_again_when_approving(demo):
    teacher, _, aid, _ = demo
    prepared = testcase_service.prepare_testset(teacher, aid, [
        {'input': '3\n1 2 3\n2\n2 4\n', 'expected': '2\n', 'expected_source': 'teacher'}
    ])
    assert testcase_service.approve_testset(teacher, aid, prepared)
    prepared['cases'][0]['input'] = '3\n1 2 3\n2\n2 4 5\n'
    with pytest.raises(ValueError, match='m=2'):
        testcase_service.approve_testset(teacher, aid, prepared)


def test_changing_sample_invalidates_approval(demo):
    teacher, _, aid, _ = demo
    prepared = testcase_service.prepare_testset(teacher, aid, [
        {'input': '3\n1 2 3\n2\n2 4\n', 'expected': '2\n', 'expected_source': 'teacher'}
    ])
    testcase_service.approve_testset(teacher, aid, prepared)
    assignment_service.update_assignment(
        teacher, aid, 'Common Elements', '',
        datetime.now(LOCAL_ZONE) + timedelta(days=3),
        sample_input=SAMPLE_IN, sample_output='different example\n')
    assert not testcase_service.get_testset(teacher, aid)['tests_approved']
    with pytest.raises(ValueError, match='changed'):
        testcase_service.approve_testset(teacher, aid, prepared)


def test_sample_output_requires_input(demo):
    teacher, _, _, _ = demo
    with pytest.raises(ValueError, match='sample stdin'):
        assignment_service.create_assignment(
            teacher, 'Another', '', datetime.now(LOCAL_ZONE) + timedelta(days=1),
            'assignment.txt', TWO_ARRAY_PROBLEM.encode(),
            sample_input='', sample_output='Hello\n')


def test_old_sqlite_database_adds_sample_output_column(tmp_path, monkeypatch):
    """The additive migration keeps legacy assignment records and values."""
    from sqlalchemy import text, inspect
    from database import init_db
    engine = create_engine('sqlite:///' + str(tmp_path / 'old.db'))
    with engine.begin() as connection:
        connection.execute(text('CREATE TABLE assignments (id INTEGER PRIMARY KEY, title TEXT)'))
        connection.execute(text("INSERT INTO assignments (id, title) VALUES (12, 'Original')"))
    monkeypatch.setattr(init_db, 'engine', engine)
    init_db.initialize_database()
    assert 'sample_output' in {col['name'] for col in inspect(engine).get_columns('assignments')}
    with engine.connect() as connection:
        assert connection.execute(text('SELECT title FROM assignments WHERE id=12')).scalar() == 'Original'
