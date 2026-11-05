"""Phase 1 tests: only successful smoke and smoke timeout dispatch."""
from agent.coordinator import dispatch_analyzers


METRIC_NAMES = {
    'correctness': 'io_tests',
    'syntax': 'compiler_warnings',
    'structure': 'static_analysis',
    'modularity': 'function_split',
    'logical': 'edge_case_runner',
    'complexity': 'complexity_estimator',
    'memory_safety': 'sanitizer_run',
    'readability': 'llm_review',
    'originality': 'similarity_flag',
}


def setup_handlers():
    calls = []

    def make_handler(name):
        def handler(context, params):
            calls.append(name)
            assert context == {'source': 'student code'}
            assert params == {}
            return {'score': 1.0}
        return handler

    handlers = {analyzer: make_handler(analyzer) for analyzer in METRIC_NAMES.values()}
    metrics = [{'name': name, 'analyzer': analyzer, 'params': {}}
               for name, analyzer in METRIC_NAMES.items()]
    return calls, handlers, metrics


def test_smoke_passes_all_nine_analyzers_run():
    calls, handlers, metrics = setup_handlers()
    first_pass = {'status': 'ready', 'smoke': {'status': 'passed'}}

    report = dispatch_analyzers(first_pass, metrics, handlers, {'source': 'student code'})

    assert report['status'] == 'full'
    assert report['flags'] == []
    assert len(calls) == 9
    assert len(report['analyzers']) == 9
    assert all(entry['status'] == 'completed' for entry in report['analyzers'])


def test_smoke_timeout_runs_only_five_source_analyzers():
    calls, handlers, metrics = setup_handlers()
    first_pass = {'status': 'blocked', 'smoke': {'status': 'timeout'}}

    report = dispatch_analyzers(first_pass, metrics, handlers, {'source': 'student code'})

    assert report['status'] == 'partial'
    assert report['flags'] == ['suspected_infinite_loop']
    assert len(calls) == 5
    assert set(calls) == {
        'compiler_warnings', 'static_analysis', 'function_split',
        'llm_review', 'similarity_flag',
    }
    skipped = [entry for entry in report['analyzers'] if entry['status'] == 'skipped']
    assert len(skipped) == 4
    assert {entry['analyzer'] for entry in skipped} == {
        'io_tests', 'edge_case_runner', 'complexity_estimator', 'sanitizer_run',
    }
