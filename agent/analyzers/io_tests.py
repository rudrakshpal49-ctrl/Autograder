"""Shared deterministic output comparator for independent correctness / logical metrics."""
from __future__ import annotations
from agent.test_runner import run_cases


def normalized_output(value: str, comparison: str = 'exact') -> str:
    """Normalize platform newlines; optionally ignore trailing line whitespace."""
    value = value.replace('\r\n', '\n').replace('\r', '\n')
    if comparison == 'trim_trailing':
        return '\n'.join(line.rstrip(' \t') for line in value.split('\n')).rstrip('\n')
    if comparison != 'exact':
        raise ValueError('output_comparison must be exact or trim_trailing.')
    return value


def grade_cases(source: bytes, cases: list[dict], *, time_limit_s: float,
                memory_limit_mb: int, runner=run_cases,
                output_comparison: str = 'exact') -> dict:
    if output_comparison not in {'exact', 'trim_trailing'}:
        raise ValueError('output_comparison must be exact or trim_trailing.')
    if not cases:
        return {'status': 'not_configured', 'score': None, 'feedback': 'No approved cases.',
                'evidence': {'total': 0, 'passed': 0, 'results': []}}
    run_result = runner(source, [case['stdin'] for case in cases],
                        time_limit_s=time_limit_s, memory_limit_mb=memory_limit_mb)
    if run_result['status'] != 'completed':
        return {'status': 'unavailable', 'score': None,
                'feedback': f"Test runner: {run_result.get('reason', run_result['status'])}",
                'evidence': {'total': len(cases), 'passed': 0, 'results': []}}
    if len(run_result.get('runs', [])) != len(cases):
        return {'status': 'unavailable', 'score': None,
                'feedback': 'Test runner returned incomplete results.',
                'evidence': {'total': len(cases), 'passed': 0, 'results': []}}
    detailed = []
    for case, actual in zip(cases, run_result['runs']):
        ok = (actual['status'] == 'ok' and
              normalized_output(actual['stdout'], output_comparison) ==
              normalized_output(case['expected_output'], output_comparison))
        detailed.append({
            'case_id': case['id'], 'source': case['source'], 'title': case.get('title', ''),
            'stdin': case['stdin'], 'expected_output': case['expected_output'],
            'actual_output': actual['stdout'], 'execution_status': actual['status'],
            'passed': ok, 'edge_type': case.get('edge_type'),
        })
    passed = sum(1 for result in detailed if result['passed'])
    return {'status': 'completed', 'score': round(passed / len(cases), 6),
            'feedback': f'{passed} of {len(cases)} approved tests passed.',
            'evidence': {'total': len(cases), 'passed': passed, 'results': detailed,
                         'output_comparison': output_comparison}}


def analyze_io_tests(context: dict, params: dict) -> dict:
    """Coordinator-compatible correctness handler."""
    return grade_cases(context['source'], context['cases'],
                       time_limit_s=context['time_limit_s'],
                       memory_limit_mb=context['memory_limit_mb'],
                       output_comparison=params.get('output_comparison', 'exact'))
