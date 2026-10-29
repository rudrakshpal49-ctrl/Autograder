"""Regression tests for automatic, versioned, teacher-controlled rubric publishing."""
from datetime import datetime, timedelta
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from database.orm_models import Assignment
from services import (authentication, assignment_service, automatic_rubric_service,
                      student_service, storage_utils)
from services.dates import LOCAL_ZONE
from services.rules_service import (validate_rules, example_rules_bytes,
                                    update_adaptive_rules)


@pytest.fixture
def demo(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "demo.db"}')
    Base.metadata.create_all(engine)
    factory = sessionmaker(bind=engine)
    for mod in [authentication, assignment_service, automatic_rubric_service, student_service]:
        monkeypatch.setattr(mod, 'SessionLocal', factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')
    teacher = authentication.create_user('teacher_demo', 'strong-password-123', 'teacher')
    outsider = authentication.create_user('other_teacher', 'strong-password-456', 'teacher')
    student = student_service.create_student(teacher, 'student_demo', 'strong-password-789', 'Student Name')
    due = datetime.now(LOCAL_ZONE) + timedelta(days=5)
    return teacher, outsider, student, due, factory, tmp_path


def make_auto(demo):
    teacher, _, _, due, _, _ = demo
    return assignment_service.create_assignment(
        teacher, 'Array maximum', 'Read n integers and print the maximum.',
        due, 'assignment.txt', b'Find the maximum element in an array.\n')


def get_metric(rules, name):
    return next(x for x in rules['metrics'] if x['name'] == name)


def test_publishing_without_json_creates_valid_versioned_defaults(demo, monkeypatch):
    # Creation must work even when the AI model is missing or unreachable.
    from agent import rubric_recommender
    monkeypatch.setattr(rubric_recommender, 'suggest_adaptive_rubric',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError('No LLM on creation')))
    teacher, _, student, _, _, _ = demo
    aid = make_auto(demo)
    state = automatic_rubric_service.get_automatic_configuration(teacher, aid)
    rules = state['rubric']
    assert state['configuration']['source'] == 'automatic_defaults'
    assert rules['adaptive_configuration']['source'] == 'automatic_defaults'
    assert get_metric(rules, 'modularity')['params']['mode'] == 'auto'
    assert get_metric(rules, 'structure')['params']['max_function_nloc'] == 40
    assert state['rules_hash']
    validate_rules(assignment_service.download_assignment_rules(aid, teacher))
    listing = assignment_service.get_all_assignments()
    assert 'rules_path' not in listing[0] and 'rules_hash' not in listing[0]
    with pytest.raises(PermissionError):
        automatic_rubric_service.get_automatic_configuration(student, aid)


def test_teacher_update_is_direct_and_preserves_approved_correctness_tests(demo):
    teacher, _, _, _, factory, _ = demo
    aid = make_auto(demo)
    old = automatic_rubric_service.get_automatic_configuration(teacher, aid)
    with factory() as session:
        a = session.get(Assignment, aid)
        a.tests_approved = True
        a.testset_json = '[{"input":"5\\n1 2 3 4 5\\n", "expected":"5\\n"}]'
        a.testset_hash = 'testset123'
        session.commit()
    saved = automatic_rubric_service.save_configuration(
        teacher, aid, expected_rules_hash=old['rules_hash'], settings={
            'max_cyclomatic': 12, 'max_function_nloc': 75,
            'modularity_mode': 'required', 'min_functions': 3})
    assert saved['changed'] and saved['rules_hash'] != old['rules_hash']
    newer = automatic_rubric_service.get_automatic_configuration(teacher, aid)
    assert newer['configuration']['source'] == 'teacher_override'
    assert get_metric(newer['rubric'], 'structure')['params']['max_function_nloc'] == 75
    assert get_metric(newer['rubric'], 'modularity')['params']['min_functions'] == 3
    assert get_metric(newer['rubric'], 'correctness')['weight'] == .2
    with factory() as session:
        a = session.get(Assignment, aid)
        assert a.tests_approved and a.testset_hash == 'testset123'
        assert a.testset_json is not None
    with pytest.raises(ValueError, match='another tab'):
        automatic_rubric_service.save_configuration(
            teacher, aid, expected_rules_hash=old['rules_hash'], reset_defaults=True)


def test_disabled_weight_can_be_restored_without_weight_drift(demo):
    teacher, _, _, _, _, _ = demo
    aid = make_auto(demo)
    start = automatic_rubric_service.get_automatic_configuration(teacher, aid)
    disabled = automatic_rubric_service.save_configuration(
        teacher, aid, expected_rules_hash=start['rules_hash'], settings={
            'max_cyclomatic': 8, 'max_function_nloc': 40,
            'modularity_mode': 'disabled', 'transfer_weight_to': 'correctness'})
    config = automatic_rubric_service.get_automatic_configuration(teacher, aid)
    assert get_metric(config['rubric'], 'modularity')['weight'] == 0
    assert get_metric(config['rubric'], 'correctness')['weight'] == .3
    automatic_rubric_service.save_configuration(
        teacher, aid, expected_rules_hash=disabled['rules_hash'], reset_defaults=True)
    restored = automatic_rubric_service.get_automatic_configuration(teacher, aid)['rubric']
    assert get_metric(restored, 'modularity')['params']['mode'] == 'auto'
    assert get_metric(restored, 'modularity')['weight'] == .1
    assert get_metric(restored, 'correctness')['weight'] == .2
    validate_rules(json.dumps(restored).encode('utf-8'))


def test_ai_advice_is_saved_immediately_but_cannot_mandate_helpers(demo, monkeypatch):
    teacher, _, _, _, _, _ = demo
    aid = make_auto(demo)
    state = automatic_rubric_service.get_automatic_configuration(teacher, aid)
    from agent import rubric_recommender
    monkeypatch.setattr(rubric_recommender, 'suggest_adaptive_rubric',
                        lambda *_args, **_kwargs: {
                            'max_cyclomatic': 5, 'max_function_nloc': 25,
                            'modularity_mode': 'required', 'min_functions': 9,
                            'reason': 'Problem may require functions.'})
    answer = automatic_rubric_service.recommend_and_save(
        teacher, aid, expected_rules_hash=state['rules_hash'])
    rules = automatic_rubric_service.get_automatic_configuration(teacher, aid)['rubric']
    assert answer['changed']
    assert rules['adaptive_configuration']['source'] == 'ollama_suggestion'
    assert get_metric(rules, 'structure')['params']['max_cyclomatic'] == 5
    assert get_metric(rules, 'modularity')['params']['mode'] == 'auto'
    assert get_metric(rules, 'modularity')['weight'] == .1


def test_bad_ai_result_or_connection_failure_never_changes_saved_rubric(demo, monkeypatch):
    teacher, _, _, _, _, _ = demo
    aid = make_auto(demo)
    original = automatic_rubric_service.get_automatic_configuration(teacher, aid)
    from agent import rubric_recommender
    monkeypatch.setattr(rubric_recommender, 'suggest_adaptive_rubric',
                        lambda *_args, **_kwargs: {
                            'max_cyclomatic': 'high', 'max_function_nloc': 35,
                            'modularity_mode': 'auto', 'reason': 'Invalid'})
    with pytest.raises(ValueError):
        automatic_rubric_service.recommend_and_save(teacher, aid, expected_rules_hash=original['rules_hash'])
    assert automatic_rubric_service.get_automatic_configuration(teacher, aid)['rules_hash'] == original['rules_hash']
    monkeypatch.setattr(rubric_recommender, 'suggest_adaptive_rubric',
                        lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError('Ollama unavailable')))
    with pytest.raises(OSError):
        automatic_rubric_service.recommend_and_save(teacher, aid, expected_rules_hash=original['rules_hash'])
    assert automatic_rubric_service.get_automatic_configuration(teacher, aid)['rules_hash'] == original['rules_hash']


def test_students_and_other_teachers_cannot_save_changes(demo):
    teacher, outsider, student, _, _, _ = demo
    aid = make_auto(demo)
    old_hash = automatic_rubric_service.get_automatic_configuration(teacher, aid)['rules_hash']
    for unauthorized in [student, outsider]:
        with pytest.raises(PermissionError):
            automatic_rubric_service.save_configuration(
                unauthorized, aid, expected_rules_hash=old_hash, reset_defaults=True)
    assert automatic_rubric_service.get_automatic_configuration(teacher, aid)['rules_hash'] == old_hash


def test_explicit_teacher_uploaded_json_is_preserved(demo):
    teacher, _, _, due, _, _ = demo
    custom = json.loads(example_rules_bytes())
    get_metric(custom, 'structure')['params']['max_cyclomatic'] = 19
    contents = json.dumps(custom).encode('utf-8')
    aid = assignment_service.create_assignment(
        teacher, 'Custom', '', due, 'task.txt', b'Write a C program.', 'custom.json', contents)
    assert assignment_service.download_assignment_rules(aid, teacher) == contents
    assert get_metric(automatic_rubric_service.get_automatic_configuration(teacher, aid)['rubric'],
                      'structure')['params']['max_cyclomatic'] == 19
