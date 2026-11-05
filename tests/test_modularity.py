"""Tests for source-only function-split analysis and coordinator dispatch."""

from types import SimpleNamespace
import sys

import pytest

from agent.analyzers import function_split, HANDLERS
from agent.coordinator import dispatch_analyzers


def functions_fixture(monkeypatch, names):
    monkeypatch.setattr(function_split, '_functions_from_lizard',
                        lambda source: [{'name': name, 'start_line': i*10+1,
                                         'end_line': i*10+5, 'length': 5,
                                         'cyclomatic': 1}
                                        for i, name in enumerate(names)])


def test_main_only_full_marks_by_default(monkeypatch):
    functions_fixture(monkeypatch, ['main'])
    result = function_split.analyze_modularity({'source': b'int main(void){return 0;}'}, {})
    assert result['score'] == 1.0
    assert result['evidence']['function_count'] == 1
    assert result['evidence']['helper_count'] == 0
    assert result['evidence']['min_functions'] == 1


def test_helpers_required_penalizes_main_only_once(monkeypatch):
    functions_fixture(monkeypatch, ['main'])
    result = function_split.analyze_modularity({'source': 'int main(){}'}, {
        'expect_helpers': True, 'min_functions': 2,
        'penalty_per_missing_function': 0.3,
    })
    assert result['score'] == pytest.approx(0.7)
    assert result['evidence']['missing_functions'] == 1
    assert 'helper' in result['feedback']


def test_helper_function_full_marks(monkeypatch):
    functions_fixture(monkeypatch, ['add', 'main'])
    result = function_split.analyze_modularity({'source': 'int add(){return 0;} int main(){return add();}'}, {
        'expect_helpers': True, 'min_functions': 2,
    })
    assert result['score'] == 1.0
    assert result['evidence']['helper_functions'] == ['add']
    assert result['evidence']['function_names'] == ['add', 'main']


def test_minimum_three_functions_and_score_floor(monkeypatch):
    functions_fixture(monkeypatch, ['main'])
    result = function_split.analyze_modularity({'source': 'int main(){}'}, {
        'expect_helpers': True, 'min_functions': 4,
        'penalty_per_missing_function': 0.5,
    })
    assert result['score'] == 0
    assert result['evidence']['missing_functions'] == 3


def test_empty_or_no_function_cannot_get_full_marks(monkeypatch):
    functions_fixture(monkeypatch, [])
    result = function_split.analyze_modularity({'source': ''}, {})
    assert result['score'] == 0
    assert result['evidence']['function_count'] == 0


@pytest.mark.parametrize('params', [
    {'expect_helpers': 1},
    {'expect_helpers': 'yes'},
    {'min_functions': 0},
    {'min_functions': True},
    {'penalty_per_missing_function': -0.1},
    {'penalty_per_missing_function': 1.1},
    {'penalty_per_missing_function': float('nan')},
    {'penalty_per_missing_function': True},
])
def test_invalid_params(params):
    with pytest.raises(ValueError):
        function_split.analyze_modularity({'source': 'int main(){}'}, params)


def test_lizard_missing_returns_unavailable(monkeypatch):
    def fail(source):
        raise RuntimeError('Lizard not installed')
    monkeypatch.setattr(function_split, '_functions_from_lizard', fail)
    result = function_split.analyze_modularity({'source': 'int main(){}'}, {})
    assert result['status'] == 'unavailable'
    assert result['score'] is None


def test_runs_after_smoke_timeout(monkeypatch):
    functions_fixture(monkeypatch, ['main'])
    report = dispatch_analyzers(
        {'status': 'blocked', 'smoke': {'status': 'timeout'}},
        [{'name': 'modularity', 'analyzer': 'function_split',
          'params': {'expect_helpers': True}}],
        HANDLERS, {'source': b'int main(void){return 0;}'})
    assert report['status'] == 'partial'
    assert report['flags'] == ['suspected_infinite_loop']
    assert report['analyzers'][0]['status'] == 'completed'
    assert report['analyzers'][0]['result']['score'] == 0.7


def test_function_declaration_does_not_count_as_definition(monkeypatch):
    """Test actual Lizard-style function_list: only definitions are returned."""
    def analyze_source_code(filename, source):
        assert filename.endswith('.c')
        assert 'int helper(int);' in source
        return SimpleNamespace(function_list=[SimpleNamespace(
            name='main', start_line=2, end_line=4, cyclomatic_complexity=1)])
    monkeypatch.setitem(sys.modules, 'lizard', SimpleNamespace(analyze_file=SimpleNamespace(
        analyze_source_code=analyze_source_code)))
    # Reuse the same parser as structure grading; declarations are not functions.
    monkeypatch.setattr(function_split, '_functions_from_lizard',
                        __import__('agent.analyzers.static_analysis', fromlist=['_functions_from_lizard'])._functions_from_lizard)
    result = function_split.analyze_modularity(
        {'source': 'int helper(int);\nint main(void) {\nreturn 0;\n}'},
        {'min_functions': 2})
    assert result['evidence']['function_count'] == 1
    assert result['score'] == 0.7
