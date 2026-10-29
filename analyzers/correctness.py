"""Problem-independent, deterministic stdout comparison against approved cases."""
from __future__ import annotations

import math


def normalize_output(value: str) -> str:
    """Default contest-style output comparison; retain internal whitespace."""
    return '\n'.join(line.rstrip(' \t') for line in value.replace('\r\n', '\n').replace('\r', '\n').split('\n')).rstrip('\n')


def outputs_equal(expected: str, actual: str, comparison: dict | None = None) -> bool:
    """Comparison policy is selected and approved per assignment, never guessed per student."""
    policy = comparison or {'mode': 'trim_trailing'}
    mode = policy.get('mode', 'trim_trailing')
    if mode == 'exact':
        return expected == actual
    if mode == 'trim_trailing':
        return normalize_output(expected) == normalize_output(actual)
    if mode == 'tokens':
        return expected.split() == actual.split()
    if mode == 'unordered_tokens':
        return sorted(expected.split()) == sorted(actual.split())
    if mode == 'unordered_lines':
        return sorted(normalize_output(expected).split('\n')) == sorted(normalize_output(actual).split('\n'))
    if mode == 'float_tokens':
        a, b = expected.split(), actual.split()
        if len(a) != len(b):
            return False
        for x, y in zip(a, b):
            try:
                n1, n2 = float(x), float(y)
                if not math.isfinite(n1) or not math.isfinite(n2):
                    return False
                if not math.isclose(n1, n2, rel_tol=policy.get('rel_tol', 1e-6),
                                    abs_tol=policy.get('abs_tol', 1e-6)):
                    return False
            except ValueError:
                if x != y:
                    return False
        return True
    raise ValueError('Unknown output comparison mode.')


def evaluate_correctness(cases: list[dict], execution: dict,
                         comparison: dict | None = None) -> dict:
    """Return 0..1 score with concrete counterexamples. No model decides marks."""
    status = execution['status']
    if status == 'unavailable':
        return {'status': 'unavailable', 'score': None, 'message': execution['message'],
                'passed': 0, 'total': len(cases), 'results': []}
    if status != 'ok':
        return {'status': 'blocked', 'score': 0.0, 'message': execution.get('message', status),
                'passed': 0, 'total': len(cases), 'results': [],
                'compiler': execution.get('compiler', {})}
    if not cases or len(execution.get('results', [])) != len(cases):
        raise ValueError('Sandbox results do not match the test set size.')
    details, passed = [], 0
    for index, (case, result) in enumerate(zip(cases, execution['results']), 1):
        allowed_outputs = [case['expected']] + case.get('accepted_outputs', [])
        success = (result['status'] == 'passed' and
                   any(outputs_equal(out, result['stdout'], comparison) for out in allowed_outputs))
        passed += bool(success)
        details.append({'number': index, 'category': case.get('category', 'normal'),
                        'status': ('passed' if success else result['status'] if result['status'] != 'passed'
                                   else 'wrong_answer'),
                        'input': case['input'], 'expected': case['expected'],
                        'actual': result['stdout'], 'stderr': result.get('stderr', ''),
                        'runtime_ms': result.get('wall_time_ms')})
    return {'status': 'complete', 'score': round(passed / len(cases), 4),
            'passed': passed, 'total': len(cases), 'results': details,
            'message': f'{passed} of {len(cases)} tests passed.'}
