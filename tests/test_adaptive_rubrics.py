"""Regression tests for fair adaptive grading and OPTIONAL LLM suggestions."""
from copy import deepcopy
import json
import pytest

from agent import rubric_recommender
from analyzers.modularity import evaluate_modularity
from analyzers.structure import evaluate_structure
from analyzers.source_metrics import analyze_functions
from services.rules_service import EXAMPLE_RULES, update_adaptive_rules, validate_rules


def metric(source, *, nloc=10, ccn=2, length=100):
    return {'status': 'ok', 'functions': [
        {'name': 'main', 'nloc': nloc, 'length': length,
         'cyclomatic_complexity': ccn, 'start_line': 1, 'end_line': length}]}


def test_nloc_is_used_instead_of_physical_lines():
    # A long function with mostly comments should NOT be penalized for length.
    result = evaluate_structure(metric(None, nloc=8, length=150),
                                {'max_function_nloc': 40, 'max_cyclomatic': 8})
    assert result['score'] == 1
    assert result['evidence']['functions'][0]['nloc'] == 8


def test_structure_soft_weighted_penalty():
    # CCN=16 vs 8 -> .5; NLOC=80 vs 40 -> .5
    assert evaluate_structure(metric(None, nloc=80, ccn=16),
        {'max_function_nloc': 40, 'max_cyclomatic': 8})['score'] == .5
    # CCN normal 100%, NLOC doubled 50% -> .7*1 + .3*.5 = .85
    assert evaluate_structure(metric(None, nloc=80, ccn=2),
        {'max_function_nloc': 40, 'max_cyclomatic': 8})['score'] == .85
    assert evaluate_structure(metric(None, nloc=80, ccn=2),
        {'max_function_nloc': 80, 'max_cyclomatic': 8})['score'] == 1
    with pytest.raises(ValueError):
        evaluate_structure(metric(None), {'max_function_nloc': False})


def test_modularity_all_modes_are_deterministic():
    solo = metric(None, nloc=80, ccn=12)
    a = evaluate_modularity(solo, {'mode': 'auto'})
    assert a['score'] == 1 and 'consider splitting' in a['feedback']
    assert evaluate_modularity(solo, {'mode': 'auto'}) == a
    assert evaluate_modularity(solo, {'mode': 'required', 'min_functions': 2})['score'] == .5
    assert evaluate_modularity(solo, {'mode': 'disabled'})['status'] == 'skipped'
    with pytest.raises(ValueError):
        evaluate_modularity(solo, {'mode': 'not-real'})


def test_teacher_rubric_editor_transfers_weight_without_mutating_original():
    rules = deepcopy(EXAMPLE_RULES)
    revised = update_adaptive_rules(rules,
        max_cyclomatic=12, max_function_nloc=75, modularity_mode='disabled',
        transfer_weight_to='correctness')
    assert revised['metrics'][0]['weight'] == .3
    modularity = next(m for m in revised['metrics'] if m['analyzer'] == 'function_split')
    structure = next(m for m in revised['metrics'] if m['analyzer'] == 'static_analysis')
    assert modularity['weight'] == 0 and modularity['params']['mode'] == 'disabled'
    assert structure['params']['max_function_nloc'] == 75
    assert rules['metrics'][0]['weight'] == .2
    validate_rules(json.dumps(revised).encode('utf-8'))
    with pytest.raises(ValueError, match='Disabled modularity'):
        invalid = deepcopy(revised)
        modularity = next(m for m in invalid['metrics'] if m['analyzer'] == 'function_split')
        modularity['weight'] = .1
        validate_rules(json.dumps(invalid).encode('utf-8'))


def test_teacher_rubric_editor_auto_and_required():
    updated = update_adaptive_rules(deepcopy(EXAMPLE_RULES),
        max_cyclomatic=7, max_function_nloc=35, modularity_mode='required', min_functions=3)
    modularity = next(m for m in updated['metrics'] if m['analyzer'] == 'function_split')
    assert modularity['params']['min_functions'] == 3
    with pytest.raises(ValueError):
        update_adaptive_rules(deepcopy(EXAMPLE_RULES), max_cyclomatic=7,
                              max_function_nloc=35, modularity_mode='disabled',
                              transfer_weight_to=None)


def test_ollama_only_suggests_not_grades(monkeypatch):
    monkeypatch.setattr(rubric_recommender, 'extract_problem_text',
                        lambda name, data: 'Write a C program to add two integers.')
    monkeypatch.setattr(rubric_recommender, '_ask_ollama',
                        lambda prompt, model=None, schema=None: {
                           'max_cyclomatic': 4, 'max_function_nloc': 30,
                           'modularity_mode': 'auto', 'min_functions': 1,
                           'reason': 'Short introductory assignment.'})
    assert rubric_recommender.suggest_adaptive_rubric('problem.pdf', b'PDF')['modularity_mode'] == 'auto'
    assert EXAMPLE_RULES['metrics'][2]['params']['max_cyclomatic'] == 8


def test_ollama_invalid_suggestion_is_not_silently_used(monkeypatch):
    monkeypatch.setattr(rubric_recommender, 'extract_problem_text',
                        lambda name, data: 'Return a sum.')
    monkeypatch.setattr(rubric_recommender, '_ask_ollama',
                        lambda prompt, model=None, schema=None: {
                            'max_cyclomatic': 0, 'max_function_nloc': 0,
                            'modularity_mode': 'required', 'min_functions': 1, 'reason': ''})
    with pytest.raises(ValueError):
        rubric_recommender.suggest_adaptive_rubric('problem.pdf', b'PDF')
