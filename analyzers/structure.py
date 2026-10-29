"""Deterministic Structure Analyzer: 70% cyclomatic complexity, 30% NLOC.

Limits are *soft*: exceeding a limit reduces a ratio, rather than causing a
zero score. Both limits are configured in the assignment JSON rubric.
"""


def _positive_int(params: dict, key: str, default: int) -> int:
    value = params.get(key, default)
    if type(value) is not int or not 1 <= value <= 10000:
        raise ValueError(f'{key} must be an integer between 1 and 10000.')
    return value


def evaluate_structure(metrics: dict, params: dict | None = None) -> dict:
    params = params or {}
    max_ccn = _positive_int(params, 'max_cyclomatic', 8)
    # Recognize existing teacher rule files: old max_function_length becomes
    # an NLOC threshold. Updated example rules use max_function_nloc.
    max_nloc = _positive_int(params, 'max_function_nloc',
                             params.get('max_function_length', 40))
    settings = {'max_cyclomatic': max_ccn, 'max_function_nloc': max_nloc,
                'ccn_weight': 0.70, 'nloc_weight': 0.30}
    if metrics.get('status') != 'ok' or not metrics.get('functions'):
        return {'status': 'unavailable', 'score': None,
                'feedback': metrics.get('message', 'Function analysis unavailable.'),
                'evidence': settings}

    evaluated, violations = [], []
    for f in metrics['functions']:
        ccn = int(f['cyclomatic_complexity'])
        # The real Lizard adapter supplies nloc. A fallback supports older
        # test fixtures but new production results always include nloc.
        nloc = int(f.get('nloc', f['length']))
        complexity_ratio = min(1.0, max_ccn / max(1, ccn))
        nloc_ratio = min(1.0, max_nloc / max(1, nloc))
        value = 0.70 * complexity_ratio + 0.30 * nloc_ratio
        evaluated.append({**f, 'nloc': nloc, 'score': round(value, 4),
                          'complexity_ratio': round(complexity_ratio, 4),
                          'nloc_ratio': round(nloc_ratio, 4)})
        if ccn > max_ccn or nloc > max_nloc:
            violations.append({'function': f['name'], 'cyclomatic_complexity': ccn, 'nloc': nloc})

    score = round(sum(f['score'] for f in evaluated) / len(evaluated), 4)
    feedback = ('All functions meet configured structure thresholds.' if not violations else
                f'{len(violations)} function(s) exceed the configured soft thresholds; '
                'review their complexity or non-comment line count.')
    return {'status': 'passed' if not violations else 'needs_improvement',
            'score': score, 'feedback': feedback,
            'evidence': {**settings, 'functions': evaluated, 'violations': violations}}
