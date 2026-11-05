"""Dispatch configured analyzers after the compile/smoke first pass.

The coordinator supports nine analyzers, including opt-in complexity profiling.
When the smoke run fails, execution analyzers are skipped while source-only
analyzers can still provide a partial report.
"""

from agent.first_pass import can_run_execution, execution_blockers

# These analyzers execute the student's C program.
EXECUTION_ANALYZERS = {
    'io_tests',
    'edge_case_runner',
    'complexity_estimator',
    'sanitizer_run',
}

# These analyzers only inspect source or compiler diagnostics.
SOURCE_ANALYZERS = {
    'compiler_warnings',
    'static_analysis',
    'function_split',
    'llm_review',
    'similarity_flag',
}


def dispatch_analyzers(first_pass: dict, metrics: list[dict],
                       handlers: dict, context: dict) -> dict:
    """Run permitted analyzers; mark execution analyzers skipped on smoke failure.

    `metrics` is the instructor's rules['metrics'] list.
    `handlers` maps analyzer names to callables taking (context, params).
    `context` carries the source, approved cases, or diagnostics as needed.
    """
    smoke_status = first_pass['smoke']['status']
    can_execute = can_run_execution(first_pass)
    timed_out = smoke_status == 'timeout'
    results = []

    for metric in metrics:
        name = metric['name']
        analyzer = metric['analyzer']
        if analyzer not in EXECUTION_ANALYZERS | SOURCE_ANALYZERS:
            raise ValueError(f'Unknown analyzer: {analyzer}')

        if analyzer in EXECUTION_ANALYZERS and not can_execute:
            results.append({
                'name': name,
                'analyzer': analyzer,
                'status': 'skipped',
                'reason': '; '.join(execution_blockers(first_pass)) or 'First pass not ready.',
                'result': {
                    'status': 'skipped', 'score': 0.0,
                    'feedback': 'Skipped due to first-pass failure or warning; grade 0/10.',
                    'evidence': {'first_pass_blocked': True},
                },
            })
            continue

        results.append({
            'name': name,
            'analyzer': analyzer,
            'status': 'completed',
            'result': handlers[analyzer](context, metric.get('params', {})),
        })

    return {
        'status': 'full' if can_execute else 'partial',
        'flags': ['suspected_infinite_loop'] if timed_out else [],
        'analyzers': results,
    }
