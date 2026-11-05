"""Teacher helper-requirement setting is stored in assignment rules and used by grading."""
import json
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from database.db import Base
from services import assignment_service, authentication, storage_utils
from services.dates import LOCAL_ZONE
from services.rules_service import get_modularity_preferences, set_modularity_preferences
from services.first_pass_service import attach_modularity_grading
from agent.analyzers import function_split


@pytest.fixture
def teacher_workspace(tmp_path, monkeypatch):
    engine = create_engine(f'sqlite:///{tmp_path / "test.db"}')
    Base.metadata.create_all(engine)
    session_factory = sessionmaker(bind=engine)
    monkeypatch.setattr(authentication, 'SessionLocal', session_factory)
    monkeypatch.setattr(assignment_service, 'SessionLocal', session_factory)
    monkeypatch.setattr(storage_utils, 'ROOT', tmp_path)
    monkeypatch.setattr(storage_utils, 'STORAGE_ROOT', tmp_path / 'storage')
    teacher = authentication.create_user('teacher', 'teacher-password', 'teacher')
    other = authentication.create_user('other', 'teacher-password', 'teacher')
    return teacher, other


def _rules(teacher, aid):
    return json.loads(assignment_service.download_assignment_rules(aid, teacher))


def _deadline():
    return datetime.now(LOCAL_ZONE) + timedelta(days=2)


def _create(teacher, rule_data=None, require_helpers=False, min_functions=2):
    return assignment_service.create_assignment(
        teacher, 'Sample', 'Description', _deadline(),
        'task.txt', b'Task', 'rubric.json' if rule_data else None, rule_data,
        '2\n', require_helpers=require_helpers, min_functions=min_functions,
    )


def _fake_lizard(monkeypatch):
    monkeypatch.setattr(function_split, '_functions_from_lizard', lambda source: [
        {'name': 'main', 'start_line': 1, 'end_line': 1, 'length': 1, 'cyclomatic': 1},
    ])


def _modularity_score(rules):
    report = {'status': 'blocked', 'smoke': {'status': 'timeout'}}
    attach_modularity_grading(report, rules, b'int main(void){return 0;}')
    return report['modularity_grading']


def test_teacher_can_disable_helper_requirement_for_existing_rubric(teacher_workspace, monkeypatch):
    teacher, _ = teacher_workspace
    _fake_lizard(monkeypatch)
    original = {
        'time_limit_s': 1, 'memory_limit_mb': 64,
        'metrics': [
            {'name': 'syntax', 'analyzer': 'compiler_warnings', 'weight': 0.3, 'params': {'penalty_per_warning': 0.2}},
            {'name': 'structure', 'analyzer': 'static_analysis', 'weight': 0.3, 'params': {'max_cyclomatic': 8}},
            {'name': 'modularity', 'analyzer': 'function_split', 'weight': 0.4,
             'params': {'expect_helpers': True, 'min_functions': 3, 'penalty_per_missing_function': 0.3}},
        ],
    }
    aid = _create(teacher, json.dumps(original).encode(), require_helpers=True, min_functions=3)
    assert _modularity_score(_rules(teacher, aid))['score'] == 0.4

    assignment_service.update_assignment(
        teacher, aid, 'Sample', 'Description', _deadline(),
        require_helpers=False,
    )
    updated = _rules(teacher, aid)
    assert updated['metrics'][0] == original['metrics'][0]
    assert updated['metrics'][1] == original['metrics'][1]
    assert updated['metrics'][2]['weight'] == 0.4
    assert updated['metrics'][2]['params']['penalty_per_missing_function'] == 0.3
    assert get_modularity_preferences(updated) == (False, 1)
    assert _modularity_score(updated)['score'] == 1.0

    assignment_service.update_assignment(
        teacher, aid, 'Sample', 'Description', _deadline(),
        require_helpers=True, min_functions=2,
    )
    assert get_modularity_preferences(_rules(teacher, aid)) == (True, 2)
    assert _modularity_score(_rules(teacher, aid))['score'] == 0.7


def test_teacher_checkbox_applies_to_default_rules_without_weight_changes(teacher_workspace, monkeypatch):
    teacher, _ = teacher_workspace
    _fake_lizard(monkeypatch)
    optional = _create(teacher, require_helpers=False)
    required = _create(teacher, require_helpers=True, min_functions=2)

    optional_rules = _rules(teacher, optional)
    required_rules = _rules(teacher, required)
    assert 'metrics' not in optional_rules
    assert 'metrics' not in required_rules
    assert _modularity_score(optional_rules)['score'] == 1.0
    assert _modularity_score(required_rules)['score'] == 0.7
    assert _modularity_score(required_rules)['rule_source'] == 'teacher_option'


def test_custom_rubric_without_modularity_is_preserved(teacher_workspace, monkeypatch):
    teacher, _ = teacher_workspace
    _fake_lizard(monkeypatch)
    initial = {'time_limit_s': 2, 'memory_limit_mb': 64, 'metrics': [
        {'name': 'syntax', 'analyzer': 'compiler_warnings', 'weight': 1.0, 'params': {}}
    ]}
    aid = _create(teacher, json.dumps(initial).encode(), require_helpers=True)
    rules = _rules(teacher, aid)
    assert rules['metrics'] == initial['metrics']
    result = _modularity_score(rules)
    assert result['score'] == 0.7
    assert result['weight'] is None  # no invented grade weight
    assignment_service.update_assignment(teacher, aid, 'Sample', '', _deadline(), require_helpers=False)
    assert _modularity_score(_rules(teacher, aid))['status'] == 'not_configured'


def test_option_is_teacher_only_and_validated(teacher_workspace):
    teacher, other = teacher_workspace
    aid = _create(teacher)
    with pytest.raises(PermissionError):
        assignment_service.update_assignment(other, aid, 'Sample', 'Description', _deadline(),
                                             require_helpers=True)
    with pytest.raises(ValueError, match='Minimum functions'):
        set_modularity_preferences(b'{"time_limit_s": 1, "memory_limit_mb": 64}', True, 0)
    assert get_modularity_preferences(_rules(teacher, aid))[0] is False


def test_helper_option_does_not_modify_rules_unless_specified(teacher_workspace):
    teacher, _ = teacher_workspace
    original = b'{"time_limit_s": 1, "memory_limit_mb": 64}'
    aid = assignment_service.create_assignment(
        teacher, 'No option', 'Description', _deadline(),
        'task.txt', b'Task', 'rubric.json', original,
    )
    assert assignment_service.download_assignment_rules(aid, teacher) == original
