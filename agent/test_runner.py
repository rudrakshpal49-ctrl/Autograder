"""Compile once, then execute a trusted C program against bounded stdin cases.

Native subprocess execution is NOT a security boundary. Only run instructor-trusted
reference code and trusted local student samples until a secure sandbox exists.
"""
from __future__ import annotations

import os
import shutil
import tempfile
from pathlib import Path

from agent.sandbox import _invoke, COMPILE_TIMEOUT_S, COMPILE_MEMORY_MB

MAX_CASES = 60
MAX_INPUT_BYTES = 8192


def run_cases(source: bytes, inputs: list[str], *, time_limit_s: float = 1,
              memory_limit_mb: int = 64) -> dict:
    if not isinstance(source, bytes) or not 0 < len(source) <= 100 * 1024:
        raise ValueError('Source must be 1–102400 bytes.')
    if not isinstance(inputs, list) or not 0 < len(inputs) <= MAX_CASES:
        raise ValueError(f'Provide between 1 and {MAX_CASES} test inputs.')
    for stdin in inputs:
        if not isinstance(stdin, str) or len(stdin.encode('utf-8')) > MAX_INPUT_BYTES:
            raise ValueError('Each stdin must be UTF-8 and no longer than 8192 bytes.')
    gcc = shutil.which('gcc')
    if not gcc:
        return {'status': 'unavailable', 'reason': 'GCC not found on PATH.', 'runs': []}
    with tempfile.TemporaryDirectory(prefix='ag_cases_') as temp:
        folder = Path(temp)
        src = folder / 'program.c'
        exe = folder / ('program.exe' if os.name == 'nt' else 'program')
        src.write_bytes(source)
        compiled = _invoke([gcc, '-O2', '-std=c11', str(src), '-o', str(exe)],
                           workdir=folder, input_file=None,
                           wall_timeout=COMPILE_TIMEOUT_S, memory_limit_mb=COMPILE_MEMORY_MB)
        if compiled['returncode'] != 0 or compiled['timed_out'] or not exe.exists():
            return {'status': 'compile_failed', 'reason': compiled['stderr'][:3000], 'runs': []}
        runs = []
        for stdin in inputs:
            path = folder / 'stdin.txt'
            path.write_bytes(stdin.encode('utf-8'))
            result = _invoke([str(exe)], workdir=folder, input_file=path,
                             wall_timeout=time_limit_s, memory_limit_mb=memory_limit_mb)
            if result['timed_out']:
                status = 'timeout'
            elif result['output_limit']:
                status = 'output_limit'
            elif result['memory_limit']:
                status = 'memory_limit'
            elif result['returncode'] == 0:
                status = 'ok'
            else:
                status = 'runtime_error'
            runs.append({'status': status, 'stdout': result['stdout'],
                         'stderr': result['stderr'][:500],
                         'exit_code': result['returncode'],
                         'wall_time_ms': result['wall_time_ms']})
        return {'status': 'completed', 'runs': runs}
