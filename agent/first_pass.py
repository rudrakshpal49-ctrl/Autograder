"""First-pass coordinator: static sanity + native GCC compile + local smoke run."""
import hashlib
from agent.sandbox import compile_and_smoke
from agent.static_sanity import analyze_static_sanity


def execution_blockers(report: dict) -> list[str]:
    """First-pass reasons that forbid running student-code analyzers.

    Compiler warnings and advisory static-sanity warnings are intentionally
    blocking because the instructor requested a strict first-pass gate.
    """
    compiler = report.get('compiler') or {}
    smoke = report.get('smoke') or {}
    sanity = report.get('static_sanity') or {}
    reasons = []
    if 'status' in compiler and compiler['status'] != 'passed':
        reasons.append('Compile check did not pass.')
    if int(compiler.get('warning_count') or 0) > 0:
        reasons.append(f"Compiler reported {compiler['warning_count']} warning(s).")
    if 'status' in sanity and sanity['status'] != 'passed':
        reasons.append(f"Static sanity: {sanity.get('status', 'missing')}.")
    if 'status' in smoke and smoke['status'] != 'passed':
        reasons.append(f"Smoke run: {smoke.get('status', 'missing')}.")
    return reasons


def can_run_execution(report: dict) -> bool:
    """Execution analyzers require a ready, warning-free first pass."""
    return report.get('status') == 'ready' and not execution_blockers(report)


def evaluate_source(source_bytes: bytes, *, sample_input: str | None,
                    time_limit_s: float = 1, memory_limit_mb: int = 64,
                    rules_hash: str | None = None) -> dict:
    source = source_bytes.decode('utf-8')
    static = analyze_static_sanity(source)
    execution = compile_and_smoke(source_bytes, sample_input or '', time_limit_s,
                                  memory_limit_mb, run_smoke=sample_input is not None)
    compiler, smoke = execution['compiler'], execution['smoke']
    warnings = int(compiler.get('warning_count') or 0) > 0
    if compiler['status'] == 'unavailable':
        overall = 'unavailable'
    elif compiler['status'] != 'passed' or static['status'] != 'passed' or warnings:
        overall = 'blocked'
    elif smoke['status'] == 'skipped':
        overall = 'incomplete'
    elif smoke['status'] != 'passed':
        overall = 'blocked'
    else:
        overall = 'ready'
    gate_reasons = execution_blockers({
        'compiler': compiler, 'smoke': smoke, 'static_sanity': static,
    })
    return {
        'status': overall, 'compiler': compiler, 'smoke': smoke, 'static_sanity': static,
        'execution_gate': {'allowed': not gate_reasons, 'reasons': gate_reasons},
        'source_hash': hashlib.sha256(source_bytes).hexdigest(),
        'rules_hash': rules_hash,
        'compiler_id': execution['compiler_id'],
        'execution_backend': 'native_gcc',
        'note': 'First pass only: successful execution does not imply correct output.',
    }
