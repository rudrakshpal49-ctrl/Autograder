"""Current schema and minimal rules remain compatible with older assignments."""
from sqlalchemy import inspect, create_engine
from database.db import Base
from services.rules_service import validate_rules, example_rules_bytes


def test_test_case_table_is_additive(tmp_path):
    engine = create_engine(f'sqlite:///{tmp_path / "fresh.db"}')
    Base.metadata.create_all(engine)
    assert set(inspect(engine).get_table_names()) == {
        'users', 'assignments', 'submissions', 'first_pass_results', 'grading_cases'
    }


def test_minimal_resource_limits_and_previous_rules_accepted():
    assert validate_rules(example_rules_bytes())['time_limit_s'] == 1
    legacy = b'{"time_limit_s":1,"memory_limit_mb":64,"metrics":[{"name":"old"}]}'
    assert validate_rules(legacy)['memory_limit_mb'] == 64
