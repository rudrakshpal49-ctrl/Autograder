"""Local GCC compile and smoke run for *trusted* C programs only.

IMPORTANT: Native execution is NOT a sandbox. Student programs run with the
same operating-system privileges as the AutoGrader process. The timeout,
output limit, and psutil memory polling here are best-effort protections,
not security isolation or guaranteed hard resource limits. Never run
untrusted or adversarial submissions using this backend.

The public compile_and_smoke() API is preserved so the existing first-pass,
Syntax and Structure modules keep working without Docker.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

import psutil

COMPILE_TIMEOUT_S = 25
COMPILE_MEMORY_MB = 384
OUTPUT_LIMIT = 64 * 1024


def check_environment() -> tuple[bool, str, str | None]:
    """Return whether a native GCC compiler can be run from this Python process."""
    gcc = shutil.which('gcc')
    if not gcc:
        return False, ('GCC not found on PATH. Install MSYS2 UCRT64 GCC and add '
                       r'C:\msys64\ucrt64\bin to your Windows PATH.'), None
    try:
        result = subprocess.run([gcc, '--version'], capture_output=True,
                                text=True, timeout=5, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False, 'GCC is installed but could not be started.', None
    if result.returncode != 0:
        return False, 'GCC could not start successfully.', None
    version = result.stdout.splitlines()[0].strip() if result.stdout else 'GCC (version unknown)'
    return True, f'Native GCC is ready: {version}', version


def _memory_usage(process: subprocess.Popen) -> int:
    """Sample current RSS of the process and its children (best effort)."""
    try:
        root = psutil.Process(process.pid)
        processes = [root, *root.children(recursive=True)]
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        return 0
    total = 0
    for child in processes:
        try:
            total += child.memory_info().rss
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return total


def _kill_tree(process: subprocess.Popen) -> None:
    """Terminate the process and its known descendants, including on Windows."""
    try:
        root = psutil.Process(process.pid)
        children = root.children(recursive=True)
        for child in children:
            try:
                child.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        try:
            root.kill()
        except (psutil.NoSuchProcess, psutil.AccessDenied):
            pass
    except (psutil.NoSuchProcess, psutil.AccessDenied):
        pass
    try:
        process.kill()
    except OSError:
        pass
    try:
        process.wait(timeout=2)
    except subprocess.TimeoutExpired:
        pass


def _invoke(command: list[str], *, workdir: Path, input_file: Path | None,
            wall_timeout: float, memory_limit_mb: int) -> dict:
    """Run a local process with timeout, output cap, and polled RSS limit."""
    out_path, err_path = workdir / 'stdout.log', workdir / 'stderr.log'
    started = time.monotonic()
    returncode = None
    timed_out = output_limit = memory_limit = False
    peak_rss_bytes = 0
    try:
        with out_path.open('wb') as stdout, err_path.open('wb') as stderr:
            with input_file.open('rb') if input_file is not None else open(os.devnull, 'rb') as stdin:
                creationflags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == 'nt' else 0
                process = subprocess.Popen(
                    command, stdin=stdin, stdout=stdout, stderr=stderr,
                    cwd=str(workdir), shell=False,
                    creationflags=creationflags,
                    start_new_session=(os.name != 'nt'),
                )
                try:
                    while process.poll() is None:
                        if time.monotonic() - started >= wall_timeout:
                            timed_out = True
                            break
                        if (out_path.stat().st_size > OUTPUT_LIMIT or
                                err_path.stat().st_size > OUTPUT_LIMIT):
                            output_limit = True
                            break
                        usage = _memory_usage(process)
                        peak_rss_bytes = max(peak_rss_bytes, usage)
                        if usage > memory_limit_mb * 1024 * 1024:
                            memory_limit = True
                            break
                        time.sleep(0.025)
                    if timed_out or output_limit or memory_limit:
                        _kill_tree(process)
                    else:
                        process.wait(timeout=2)
                finally:
                    if process.poll() is None:
                        _kill_tree(process)
                returncode = process.returncode
        # Detect oversized output even if the program exited before our next poll.
        if out_path.stat().st_size > OUTPUT_LIMIT or err_path.stat().st_size > OUTPUT_LIMIT:
            output_limit = True
        stdout_text = out_path.read_bytes()[:OUTPUT_LIMIT].decode('utf-8', 'replace')
        stderr_text = err_path.read_bytes()[:OUTPUT_LIMIT].decode('utf-8', 'replace')
    except (OSError, subprocess.TimeoutExpired) as exc:
        stdout_text = ''
        stderr_text = str(exc)
    return {
        'returncode': returncode, 'stdout': stdout_text, 'stderr': stderr_text,
        'wall_time_ms': round((time.monotonic() - started) * 1000, 2),
        'timed_out': timed_out, 'output_limit': output_limit,
        'memory_limit': memory_limit,
        'peak_rss_mb': round(peak_rss_bytes / (1024 * 1024), 2),
    }


def compile_and_smoke(source_bytes: bytes, sample_input: str,
                      time_limit_s: float = 1, memory_limit_mb: int = 64,
                      run_smoke: bool = True) -> dict:
    """Compile with native GCC and optionally execute the compiled program locally.

    This is intended only for your own trusted test sources. It does not
    provide Docker-equivalent isolation and does not safely grade unknown code.
    """
    if not isinstance(source_bytes, bytes) or not 0 < len(source_bytes) <= 100 * 1024:
        raise ValueError('Invalid C source size.')
    if not isinstance(sample_input, str) or len(sample_input.encode('utf-8')) > 8192:
        raise ValueError('Sample input cannot exceed 8 KiB.')
    if (isinstance(time_limit_s, bool) or not isinstance(time_limit_s, (int, float))
            or not 0 < time_limit_s <= 30 or isinstance(memory_limit_mb, bool)
            or not isinstance(memory_limit_mb, int) or not 16 <= memory_limit_mb <= 4096):
        raise ValueError('Unsupported execution resource limits.')

    ready, message, gcc_version = check_environment()
    if not ready:
        return {
            'compiler': {'status': 'unavailable', 'message': message},
            'smoke': {'status': 'skipped', 'message': 'Native GCC unavailable.'},
            'compiler_id': None, 'image_id': None,
        }

    gcc = shutil.which('gcc')
    with tempfile.TemporaryDirectory(prefix='ag_native_') as temporary:
        folder = Path(temporary)
        source = folder / 'student.c'
        executable = folder / ('student.exe' if os.name == 'nt' else 'student')
        source.write_bytes(source_bytes)
        compile_cmd = [gcc, '-O2', '-Wall', '-Wextra', '-std=c11', str(source), '-o', str(executable)]
        compilation = _invoke(compile_cmd, workdir=folder, input_file=None,
                              wall_timeout=COMPILE_TIMEOUT_S, memory_limit_mb=COMPILE_MEMORY_MB)
        if compilation['timed_out']:
            state, description = 'timeout', 'GCC compilation exceeded the time limit.'
        elif compilation['output_limit']:
            state, description = 'failed', 'GCC generated excessive output.'
        elif compilation['memory_limit']:
            state, description = 'failed', 'GCC exceeded the monitored compilation memory limit.'
        elif compilation['returncode'] == 0 and executable.is_file():
            state, description = 'passed', 'Compilation succeeded.'
        else:
            state, description = 'failed', 'Compilation failed. Review GCC diagnostics.'

        diagnostics = (compilation['stderr'] + '\n' + compilation['stdout']).strip()[:OUTPUT_LIMIT]
        compiler = {
            'status': state, 'message': description, 'exit_code': compilation['returncode'],
            'diagnostics': diagnostics,
            'warning_count': len(re.findall(r'\bwarning:', compilation['stderr'])),
            'wall_time_ms': compilation['wall_time_ms'],
        }
        if state != 'passed':
            smoke = {'status': 'skipped', 'message': 'Compile check did not pass.'}
        elif not run_smoke:
            smoke = {'status': 'skipped', 'message': 'Teacher has not configured sample input.'}
        else:
            input_path = folder / 'sample_input.txt'
            input_path.write_bytes(sample_input.encode('utf-8'))
            outcome = _invoke([str(executable)], workdir=folder, input_file=input_path,
                              wall_timeout=time_limit_s, memory_limit_mb=memory_limit_mb)
            if outcome['timed_out']:
                status, description = 'timeout', 'Program exceeded the smoke-run time limit.'
            elif outcome['output_limit']:
                status, description = 'output_limit', 'Program produced excessive output.'
            elif outcome['memory_limit']:
                status, description = 'resource_limit', 'Program exceeded the monitored memory limit.'
            elif outcome['returncode'] == 0:
                status, description = 'passed', 'Program exited normally (correctness not checked).'
            else:
                status, description = 'crashed', 'Program exited with a nonzero status.'
            smoke = {
                'status': status, 'message': description, 'exit_code': outcome['returncode'],
                'stdout': outcome['stdout'], 'stderr': outcome['stderr'],
                'wall_time_ms': outcome['wall_time_ms'],
                'peak_rss_mb': outcome['peak_rss_mb'],
                'time_limit_s': time_limit_s, 'memory_limit_mb': memory_limit_mb,
            }
        return {
            'compiler': compiler, 'smoke': smoke,
            'compiler_id': gcc_version, 'image_id': None,
        }
