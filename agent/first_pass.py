"""First-pass coordinator: static sanity + Docker compile + Docker smoke test."""
import hashlib
from agent.sandbox import compile_and_smoke
from agent.static_sanity import analyze_static_sanity


def evaluate_source(source_bytes: bytes, *, sample_input: str | None,
                    time_limit_s: float = 1, memory_limit_mb: int = 64,
                    rules_hash: str | None = None) -> dict:
    source = source_bytes.decode('utf-8')
    static = analyze_static_sanity(source)
    execution = compile_and_smoke(source_bytes, sample_input or '', time_limit_s,
                                  memory_limit_mb, run_smoke=sample_input is not None)
    compiler, smoke = execution['compiler'], execution['smoke']
    if compiler['status'] == 'unavailable':
        overall = 'unavailable'
    elif compiler['status'] != 'passed' or static['status'] == 'failed':
        overall = 'blocked'
    elif smoke['status'] in {'skipped'}:
        overall = 'incomplete'
    elif smoke['status'] != 'passed':
        overall = 'blocked'
    else:
        overall = 'ready'
    return {
        'status': overall, 'compiler': compiler, 'smoke': smoke, 'static_sanity': static,
        'source_hash': hashlib.sha256(source_bytes).hexdigest(),
        'rules_hash': rules_hash,
        'image_id': execution['image_id'],
        'note': 'First pass only: successful execution does not imply correct output.',
    }
