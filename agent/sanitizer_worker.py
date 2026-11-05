"""One Linux GCC ASan/UBSan worker, used natively or through Windows WSL2.

Wire protocol (WSL): UTF-8 JSON on stdin; one JSON object on stdout.
Only the WSL/Python-GCC worker owns compilation and sanitizer execution.
IMPORTANT: WSL is not a security sandbox for arbitrary/untrusted programs.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time

COMPILE_TIMEOUT_S = 25
OUTPUT_LIMIT = 64 * 1024
ALLOWED_FLAGS = {'-fsanitize=address,undefined', '-fsanitize=address', '-fsanitize=undefined'}


def _rss_bytes(pid: int) -> int:
    """Best-effort Linux RSS sampling; ASan reserves huge VIRTUAL address space."""
    try:
        for line in Path(f'/proc/{pid}/status').read_text().splitlines():
            if line.startswith('VmRSS:'):
                return int(line.split()[1]) * 1024
    except (OSError, IndexError, ValueError):
        pass
    return 0


def _invoke(command: list[str], workdir: Path, *, stdin: bytes = b'',
            timeout: float, memory_mb: int) -> dict:
    out = workdir / 'stdout.log'
    err = workdir / 'stderr.log'
    start = time.monotonic()
    timed_out = memory_limit = output_limit = False
    exit_code = None
    env = os.environ.copy()
    env['ASAN_OPTIONS'] = 'detect_leaks=1:halt_on_error=1:abort_on_error=0'
    env['UBSAN_OPTIONS'] = 'halt_on_error=1:print_stacktrace=1'
    input_file = workdir / 'stdin.txt'
    input_file.write_bytes(stdin)
    with out.open('wb') as stdout, err.open('wb') as stderr, input_file.open('rb') as input_stream:
        try:
            proc = subprocess.Popen(command, cwd=str(workdir), stdin=input_stream,
                                    stdout=stdout, stderr=stderr, env=env,
                                    start_new_session=True)
        except OSError as exc:
            return {'returncode': None, 'stderr': str(exc), 'stdout': '',
                    'timed_out': False, 'memory_limit': False,
                    'output_limit': False, 'wall_time_ms': 0}
        try:
            while proc.poll() is None:
                if time.monotonic() - start > timeout:
                    timed_out = True
                    break
                if out.stat().st_size + err.stat().st_size > OUTPUT_LIMIT:
                    output_limit = True
                    break
                if _rss_bytes(proc.pid) > memory_mb * 1024 * 1024:
                    memory_limit = True
                    break
                time.sleep(0.02)
        finally:
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
            try:
                exit_code = proc.wait(timeout=2)
            except subprocess.TimeoutExpired:
                proc.kill()
                exit_code = proc.wait(timeout=2)
    return {'returncode': exit_code, 'stdout': out.read_bytes()[:OUTPUT_LIMIT].decode('utf-8', 'replace'),
            'stderr': err.read_bytes()[:OUTPUT_LIMIT].decode('utf-8', 'replace'),
            'timed_out': timed_out, 'memory_limit': memory_limit,
            'output_limit': output_limit,
            'wall_time_ms': int((time.monotonic() - start) * 1000)}


def _ok(run: dict) -> bool:
    return run.get('returncode') == 0 and not any(run.get(k) for k in
           ('timed_out', 'memory_limit', 'output_limit'))


def _compile(gcc: str, folder: Path, filename: str, source: bytes, flags: str) -> dict:
    src = folder / filename
    exe = folder / 'program'
    src.write_bytes(source)
    cmd = [gcc, '-std=c11', '-g', '-O0', '-fno-omit-frame-pointer',
           '-fno-sanitize-recover=all', flags, str(src), '-o', str(exe)]
    return _invoke(cmd, folder, timeout=COMPILE_TIMEOUT_S, memory_mb=1024)


def execute(payload: dict) -> dict:
    """Return a JSON-compatible summary; failure to run tools never implies a clean grade."""
    flags = payload['flags']
    if flags not in ALLOWED_FLAGS:
        raise ValueError('Unapproved sanitizer flags.')
    source = base64.b64decode(payload['source_b64'], validate=True)
    inputs = payload['inputs']
    time_limit_s = payload['time_limit_s']
    memory_limit_mb = payload['memory_limit_mb']
    if not isinstance(source, bytes) or not 0 < len(source) <= 102400:
        raise ValueError('Invalid C source size.')
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 60 or any(
            not isinstance(s, str) or len(s.encode('utf-8')) > 8192 for s in inputs):
        raise ValueError('Invalid approved inputs.')
    if not isinstance(time_limit_s, (int, float)) or not 0 < time_limit_s <= 30:
        raise ValueError('Invalid time limit.')
    if not isinstance(memory_limit_mb, int) or not 16 <= memory_limit_mb <= 4096:
        raise ValueError('Invalid memory limit.')
    gcc = shutil.which('gcc')
    if not gcc:
        return {'status': 'unavailable', 'runs': [],
                'reason': 'Linux GCC not found. Inside Ubuntu run: sudo apt install build-essential'}
    rss = min(4096, max(384, memory_limit_mb * 4))
    wall = min(90, max(3, time_limit_s * 3))
    with tempfile.TemporaryDirectory(prefix='ag_gcc_sanitize_') as directory:
        folder = Path(directory)
        probe_dir = folder / 'probe'
        probe_dir.mkdir()
        probe = _compile(gcc, probe_dir, 'probe.c', b'int main(void) { return 0; }\n', flags)
        if not _ok(probe) or not (probe_dir / 'program').is_file():
            msg = (probe.get('stderr') or probe.get('stdout') or 'Compiler could not produce binary.')[:900]
            return {'status': 'unavailable', 'runs': [],
                    'reason': 'Linux GCC sanitizer compile probe failed: ' + msg}
        probe_run = _invoke([str(probe_dir / 'program')], probe_dir, timeout=6, memory_mb=rss)
        if not _ok(probe_run):
            msg = (probe_run.get('stderr') or probe_run.get('stdout') or
                   f'Exit code {probe_run.get("returncode")}')[:900]
            return {'status': 'unavailable', 'runs': [],
                    'reason': 'Linux sanitizer runtime probe failed: ' + msg}
        build_dir = folder / 'build'
        build_dir.mkdir()
        build = _compile(gcc, build_dir, 'student.c', source, flags)
        if not _ok(build) or not (build_dir / 'program').is_file():
            msg = (build.get('stderr') or build.get('stdout') or 'Compilation failed.')[:900]
            return {'status': 'unavailable', 'runs': [], 'reason': 'Sanitized compilation failed: ' + msg}
        runs = []
        for i, test_input in enumerate(inputs):
            case_dir = folder / f'case_{i}'
            case_dir.mkdir()
            run = _invoke([str(build_dir / 'program')], case_dir,
                          stdin=test_input.encode('utf-8'), timeout=wall, memory_mb=rss)
            if run['timed_out']:
                status = 'timeout'
                reason = f'Exceeded sanitizer wall timeout ({wall:g}s).'
            elif run['output_limit']:
                status = 'output_limit'
                reason = 'Sanitizer or program output exceeded its limit.'
            elif run['memory_limit']:
                status = 'memory_limit'
                reason = 'Sanitized process exceeded its resident-memory allowance.'
            else:
                status = 'ok' if run['returncode'] == 0 else 'nonzero_exit'
                reason = '' if status == 'ok' else f'Exited with code {run["returncode"]}.'
            runs.append({'status': status, 'reason': reason, 'exit_code': run['returncode'],
                         'stderr': run['stderr'][:10000], 'stdout': run['stdout'][:300],
                         'wall_time_ms': run['wall_time_ms']})
        return {'status': 'completed', 'tool': 'asan_ubsan', 'runs': runs,
                'flags': flags, 'requested_flags': flags,
                'compiler': gcc, 'sanitizer_memory_limit_mb': rss,
                'sanitizer_time_limit_s': wall,
                'coverage_note': ('GCC ASan/UBSan under Linux; results are limited to approved '
                                  'inputs. RSS monitoring is best effort, not isolation.')}


def main() -> None:
    try:
        request = json.load(sys.stdin)
        result = execute(request)
    except Exception as exc:
        result = {'status': 'unavailable', 'runs': [],
                  'reason': f'Linux sanitizer worker failed: {type(exc).__name__}: {exc}'[:1100]}
    json.dump(result, sys.stdout, ensure_ascii=True)
    sys.stdout.write('\n')


if __name__ == '__main__':
    main()
