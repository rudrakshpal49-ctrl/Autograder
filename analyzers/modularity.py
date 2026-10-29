"""Deterministic Modularity Analyzer with teacher-selectable grading modes.

* auto: a helper is NEVER required; complex functions get advice only.
* required: grade explicit instructor minimum and helper requirement.
* disabled: skip metric (its rubric weight must be assigned elsewhere).
"""


def _positive_int(params, key, default):
    value = params.get(key, default)
    if type(value) is not int or not 1 <= value <= 10000:
        raise ValueError(f'{key} must be a positive integer up to 10000.')
    return value


def evaluate_modularity(metrics: dict, params: dict | None = None) -> dict:
    params = params or {}
    mode = params.get('mode', 'required' if params.get('expect_helpers', False) else 'auto')
    if mode not in ('auto', 'required', 'disabled'):
        raise ValueError('Modularity mode must be auto, required or disabled.')
    minimum = _positive_int(params, 'min_functions', 2 if mode == 'required' else 1)
    if minimum > 100:
        raise ValueError('min_functions cannot exceed 100.')

    if mode == 'disabled':
        return {'status': 'skipped', 'score': None,
                'feedback': 'Modularity is disabled by the teacher; no marks allocated.',
                'evidence': {'mode': mode, 'required_functions': 0}}
    if metrics.get('status') != 'ok' or not metrics.get('functions'):
        return {'status': 'unavailable', 'score': None,
                'feedback': metrics.get('message', 'Function analysis unavailable.'),
                'evidence': {'mode': mode, 'required_functions': minimum}}

    functions = metrics['functions']
    count = len(functions)
    helpers = sum(f['name'] != 'main' for f in functions)
    names = [f['name'] for f in functions]
    if mode == 'required':
        required = max(2, minimum)
        score = round(min(1.0, count / required), 4)
        if helpers == 0:
            score = min(score, 0.5)
        feedback = f'Found {count} of {required} required functions.'
        if helpers == 0:
            feedback += ' The assignment explicitly requires at least one helper.'
    else:
        # Prevent duplicate penalties: complexity and length affect Structure;
        # they trigger Modularity *suggestions*, not additional deductions.
        required = 1
        score = 1.0
        limit_ccn = _positive_int(params, 'suggest_if_ccn_over', 10)
        limit_nloc = _positive_int(params, 'suggest_if_nloc_over', 60)
        candidates = [f['name'] for f in functions
                      if f['cyclomatic_complexity'] > limit_ccn or
                      f.get('nloc', f['length']) > limit_nloc]
        if helpers == 0 and candidates:
            feedback = ('Helpers are optional; consider splitting complex functions: '
                        + ', '.join(candidates) + '.')
        else:
            feedback = 'Helpers are optional and there is no mandatory function count.'

    return {'status': 'passed' if score == 1.0 else 'needs_improvement',
            'score': score, 'feedback': feedback,
            'evidence': {'mode': mode, 'function_count': count, 'helper_count': helpers,
                         'required_functions': required, 'function_names': names}}
