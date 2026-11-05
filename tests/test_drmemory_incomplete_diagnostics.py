"""Regression checks for inconclusive tests (previously Dr. Memory-specific)."""
from agent.analyzers.sanitizer_run import grade_memory_safety


def _cases():
    return [{'id': 1, 'title': 'edge', 'category': 'edge', 'stdin': '1\n'}]


def test_missing_results_are_unavailable():
    r = grade_memory_safety(b'int main(){return 0;}', _cases(),
                            time_limit_s=1, memory_limit_mb=64,
                            runner=lambda *a, **k: {'status': 'unavailable', 'reason': 'WSL2 offline', 'runs': []})
    assert r['status'] == 'unavailable' and r['score'] is None
    assert 'WSL2' in r['feedback']


def test_timeout_report_is_diagnostic_and_ungraded():
    r = grade_memory_safety(b'int main(){return 0;}', _cases(),
                            time_limit_s=1, memory_limit_mb=64,
                            runner=lambda *a, **k: {
                                'status': 'completed', 'tool': 'asan_ubsan',
                                'runs': [{'status': 'timeout', 'stderr': '', 'reason': 'Exceeded 3s'}]})
    assert r['status'] == 'incomplete' and r['score'] is None
    assert r['evidence']['attempted'] == 1 and r['evidence']['completed_checks'] == 0
    assert 'Exceeded 3s' in r['evidence']['inconclusive_cases'][0]['reason']


def test_all_completed_no_findings_can_be_graded():
    r = grade_memory_safety(b'int main(){return 0;}', _cases(),
                            time_limit_s=1, memory_limit_mb=64,
                            runner=lambda *a, **k: {
                                'status': 'completed', 'tool': 'asan_ubsan',
                                'runs': [{'status': 'ok', 'stderr': ''}]})
    assert r['status'] == 'completed' and r['score'] == 1.0
