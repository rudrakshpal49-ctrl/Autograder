"""Validate teacher-supplied JSON grading rubrics without executing them."""
import json
import math
import re
from copy import deepcopy
from services.storage_utils import MAX_RULES_BYTES

ANALYZERS = {
    'io_tests', 'compiler_warnings', 'static_analysis', 'function_split',
    'edge_case_runner', 'complexity_estimator', 'sanitizer_run',
    'llm_review', 'similarity_flag',
}

EXAMPLE_RULES = {
    'problem': 'example_assignment',
    'time_limit_s': 1,
    'memory_limit_mb': 64,
    'metrics': [
        {'name': 'correctness', 'analyzer': 'io_tests', 'weight': 0.2, 'params': {'testset_source': 'approved_assignment_tests'}},
        {'name': 'syntax', 'analyzer': 'compiler_warnings', 'weight': 0.1, 'params': {'flags': '-Wall -Wextra -std=c11'}},
        {'name': 'structure', 'analyzer': 'static_analysis', 'weight': 0.1, 'params': {'max_cyclomatic': 8, 'max_function_nloc': 40}},
        {'name': 'modularity', 'analyzer': 'function_split', 'weight': 0.1, 'params': {'mode': 'auto', 'suggest_if_ccn_over': 10, 'suggest_if_nloc_over': 60}},
        {'name': 'logical', 'analyzer': 'edge_case_runner', 'weight': 0.1, 'params': {'cases': ['negatives', 'zero', 'int_overflow']}},
        {'name': 'complexity', 'analyzer': 'complexity_estimator', 'weight': 0.1, 'params': {'required': 'O(n)', 'sizes': [10, 100, 1000]}},
        {'name': 'memory_safety', 'analyzer': 'sanitizer_run', 'weight': 0.1, 'params': {'flags': '-fsanitize=address,undefined'}},
        {'name': 'readability', 'analyzer': 'llm_review', 'weight': 0.1, 'params': {'temperature': 0, 'seed': 42}},
        {'name': 'originality', 'analyzer': 'similarity_flag', 'weight': 0.1, 'params': {'flag_threshold': 0.85}},
    ],
}


def example_rules_bytes():
    return (json.dumps(EXAMPLE_RULES, indent=2) + '\n').encode('utf-8')


def validate_rules(content: bytes):
    if not isinstance(content, bytes) or not content or len(content) > MAX_RULES_BYTES:
        raise ValueError('Rules must be a nonempty JSON file up to 128 KiB.')
    try:
        rules = json.loads(content.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Rules must contain valid UTF-8 JSON.') from exc
    if not isinstance(rules, dict):
        raise ValueError('The JSON rules root must be an object.')
    for field, upper in (('time_limit_s', 30), ('memory_limit_mb', 4096)):
        v = rules.get(field)
        if isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0 or v > upper:
            raise ValueError(f'{field} must be a positive number no greater than {upper}.')
        if field == 'memory_limit_mb' and (not isinstance(v, int) or v < 16):
            raise ValueError('memory_limit_mb must be an integer between 16 and 4096 for Docker.')
    metrics = rules.get('metrics')
    if not isinstance(metrics, list) or not metrics:
        raise ValueError('Add at least one grading metric.')
    names = set()
    total = 0.0
    for index, metric in enumerate(metrics, 1):
        if not isinstance(metric, dict):
            raise ValueError(f'Metric #{index} must be an object.')
        name, analyzer, params, weight = (metric.get(k) for k in ('name', 'analyzer', 'params', 'weight'))
        if not isinstance(name, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', name):
            raise ValueError(f'Metric #{index} needs a lowercase identifier as its name.')
        if name in names:
            raise ValueError(f'Duplicate metric name: {name}.')
        names.add(name)
        if analyzer not in ANALYZERS:
            raise ValueError(f'Analyzer {analyzer!r} is not supported by the planned grading engine.')
        if params is not None and not isinstance(params, dict):
            raise ValueError(f'Parameters for {name} must be an object.')
        if isinstance(weight, bool) or not isinstance(weight, (int, float)) or not math.isfinite(weight) or not 0 <= weight <= 1:
            raise ValueError(f'Weight for {name} must be between 0 and 1.')
        if analyzer == 'function_split':
            chosen_mode = (params or {}).get('mode', 'required' if (params or {}).get('expect_helpers', False) else 'auto')
            if chosen_mode not in ('auto', 'required', 'disabled'):
                raise ValueError('Modularity mode must be auto, required or disabled.')
            if chosen_mode == 'disabled' and weight != 0:
                raise ValueError('Disabled modularity must have weight 0. Redistribute its weight to other metrics.')
        total += weight
    if not math.isclose(total, 1.0, abs_tol=0.000001):
        raise ValueError(f'Metric weights must add up to 1.0; current sum: {total:.4f}.')
    return rules


def update_adaptive_rules(rules: dict, *, max_cyclomatic: int, max_function_nloc: int,
                          modularity_mode: str, min_functions: int = 2,
                          suggest_if_ccn_over: int = 10, suggest_if_nloc_over: int = 60,
                          transfer_weight_to: str | None = None) -> dict:
    """Build an instructor-reviewable rubric; never silently change stored rules.

    Disabling modularity moves its weight to the explicitly selected OTHER metric.
    Re-enabling a zero-weight metric requires manually allocating weight.
    """
    proposal = deepcopy(rules)
    # Check incoming rules and only modify known metric types.
    validate_rules((json.dumps(proposal)).encode('utf-8'))
    for key, value in {
        'max_cyclomatic': max_cyclomatic,
        'max_function_nloc': max_function_nloc,
        'min_functions': min_functions,
        'suggest_if_ccn_over': suggest_if_ccn_over,
        'suggest_if_nloc_over': suggest_if_nloc_over,
    }.items():
        if type(value) is not int or not 1 <= value <= (100 if key == 'min_functions' else 10000):
            raise ValueError(f'{key} must be a positive integer within the allowed range.')
    if modularity_mode not in ('auto', 'required', 'disabled'):
        raise ValueError('Choose auto, required or disabled modularity.')
    source = next((m for m in proposal['metrics'] if m['analyzer'] == 'static_analysis'), None)
    modularity = next((m for m in proposal['metrics'] if m['analyzer'] == 'function_split'), None)
    if not source or not modularity:
        raise ValueError('The rubric must enable both Structure and Modularity metrics to edit them here.')

    structure_params = dict(source.get('params') or {})
    structure_params.pop('max_function_length', None)
    structure_params.update({'max_cyclomatic': max_cyclomatic, 'max_function_nloc': max_function_nloc})
    source['params'] = structure_params

    modularity_params = dict(modularity.get('params') or {})
    original_mode = modularity_params.get('mode', 'auto')
    modularity_params.pop('expect_helpers', None)
    modularity_params.update({'mode': modularity_mode,
                              'min_functions': min_functions,
                              'suggest_if_ccn_over': suggest_if_ccn_over,
                              'suggest_if_nloc_over': suggest_if_nloc_over})

    if modularity_mode != 'disabled' and modularity['weight'] == 0:
        # Restore the previously allocated metric weight, if the instructor
        # disabled modularity earlier. Its donor is saved when disabling.
        donor_name = modularity_params.get('weight_donor', 'correctness')
        previously = modularity_params.get('previous_weight', 0.1)
        if isinstance(previously, bool) or not isinstance(previously, (int, float)) or not 0 < previously <= 1:
            raise ValueError('Stored modularity weight is invalid.')
        donor = next((m for m in proposal['metrics'] if m['name'] == donor_name
                      and m is not modularity), None)
        if donor is None or round(donor['weight'] - previously, 10) < 0:
            raise ValueError('Cannot re-enable modularity automatically: the original weight donor lacks enough weight.')
        donor['weight'] = round(donor['weight'] - previously, 10)
        modularity['weight'] = previously
        modularity_params.pop('weight_donor', None)
        modularity_params.pop('previous_weight', None)

    if modularity_mode == 'disabled' and modularity['weight']:
        recipient = next((m for m in proposal['metrics'] if m['name'] == transfer_weight_to
                          and m is not modularity), None)
        if recipient is None:
            raise ValueError('Choose another enabled metric to receive the modularity weight.')
        modularity_params['previous_weight'] = modularity['weight']
        modularity_params['weight_donor'] = recipient['name']
        recipient['weight'] = round(recipient['weight'] + modularity['weight'], 10)
        modularity['weight'] = 0
    modularity['params'] = modularity_params
    # In particular, any disabled metric MUST have zero weight before publishing.
    validate_rules(json.dumps(proposal).encode('utf-8'))
    return proposal


def automatic_rules_bytes() -> bytes:
    """Ready-to-use, fixed beginner-friendly defaults for a new assignment.

    No AI call is necessary; the rubric is versioned once and shared by all
    submissions. An instructor may optionally override it later.
    """
    rules = deepcopy(EXAMPLE_RULES)
    rules['adaptive_configuration'] = {
        'source': 'automatic_defaults',
        'reason': 'Fixed soft NLOC and cyclomatic limits; helper functions are optional.',
    }
    return (json.dumps(rules, indent=2, ensure_ascii=False) + '\n').encode('utf-8')


def adaptive_rules_from_settings(rules: dict, *, max_cyclomatic: int,
                                  max_function_nloc: int, modularity_mode: str,
                                  min_functions: int = 2,
                                  suggest_if_ccn_over: int = 10,
                                  suggest_if_nloc_over: int = 60,
                                  transfer_weight_to: str = 'correctness',
                                  source: str = 'teacher_override',
                                  reason: str = '') -> dict:
    """Update adaptive metrics only; preserve every unrelated grading setting."""
    revised = update_adaptive_rules(
        rules, max_cyclomatic=max_cyclomatic,
        max_function_nloc=max_function_nloc,
        modularity_mode=modularity_mode, min_functions=min_functions,
        suggest_if_ccn_over=suggest_if_ccn_over,
        suggest_if_nloc_over=suggest_if_nloc_over,
        transfer_weight_to=transfer_weight_to,
    )
    revised['adaptive_configuration'] = {
        'source': source,
        'reason': reason[:200],
    }
    validate_rules(json.dumps(revised).encode('utf-8'))
    return revised
