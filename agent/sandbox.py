"""Run untrusted C solely in short-lived, restricted Linux Docker containers.

No local GCC execution fallback exists by design. Docker's default container
isolation is a useful academic safety boundary, NOT a guarantee against all
host compromise. Do not expose this prototype to the public internet.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

IMAGE = 'gcc:13'
COMPILE_TIMEOUT_S = 25
DOCKER_STARTUP_GRACE_S = 15
OUTPUT_LIMIT = 64 * 1024


def check_environment() -> tuple[bool, str, str | None]:
    """Require installed + running Docker and an explicitly pre-pulled image."""
    if not shutil.which('docker'):
        return False, 'Docker is not installed. Install Docker Desktop and start it.', None
    try:
        status = subprocess.run(['docker', 'info', '--format', '{{.ServerVersion}}'],
                                capture_output=True, text=True, timeout=8, check=False)
        if status.returncode != 0:
            return False, 'Docker engine is not running. Open Docker Desktop.', None
        image = subprocess.run(['docker', 'image', 'inspect', IMAGE, '--format', '{{.Id}}'],
                               capture_output=True, text=True, timeout=8, check=False)
        if image.returncode != 0:
            return False, f'Docker image missing. Run: docker pull {IMAGE}', None
        return True, 'Docker is ready.', image.stdout.strip()
    except (OSError, subprocess.TimeoutExpired):
        return False, 'Could not contact Docker. Check Docker Desktop is running.', None


def _docker_cmd(*, memory_mb: int, mounts: list[str], cidfile: Path) -> list[str]:
    """Consistent restrictions for compile and execution containers."""
    return [
        'docker', 'run', '--rm', '--pull=never', '--cidfile', str(cidfile),
        '--network=none', '--read-only', '--cap-drop=ALL',
        '--security-opt=no-new-privileges', '--pids-limit=64', '--cpus=1',
        '--memory', f'{memory_mb}m', '--memory-swap', f'{memory_mb}m',
        '--user=65534:65534', '--ulimit=nofile=64:64',
        '--tmpfs=/tmp:rw,nosuid,nodev,size=64m',
        *mounts, IMAGE,
    ]


def _bind(path: Path, target: str, read_only: bool = True) -> list[str]:
    # Paths are always created by our code, never supplied by students.
    # --mount avoids implicit creation of source paths.
    mode = ',readonly' if read_only else ''
    return ['--mount', f'type=bind,source={path.resolve()},target={target}{mode}']


def _terminate_container(cidfile: Path):
    try:
        cid = cidfile.read_text(encoding='ascii').strip()
        if cid and all(c in '0123456789abcdef' for c in cid):
            subprocess.run(['docker', 'rm', '-f', cid], capture_output=True, timeout=6, check=False)
    except (OSError, subprocess.TimeoutExpired):
        pass


def _invoke(command: list[str], cidfile: Path, *, wall_timeout: float) -> dict:
    """Bound attached stdout/stderr; force-remove container on abnormal exit."""
    with tempfile.TemporaryDirectory(prefix='ag_io_') as temp:
        out_path = Path(temp) / 'stdout'
        err_path = Path(temp) / 'stderr'
        start = time.monotonic()
        try:
            with out_path.open('wb') as out, err_path.open('wb') as err:
                proc = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=out, stderr=err)
                timed_out = False
                over_output = False
                while proc.poll() is None:
                    if time.monotonic() - start > wall_timeout:
                        timed_out = True
                        proc.kill()
                        break
                    if out_path.stat().st_size > OUTPUT_LIMIT or err_path.stat().st_size > OUTPUT_LIMIT:
                        over_output = True
                        proc.kill()
                        break
                    time.sleep(0.04)
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    proc.kill()
                    proc.wait()
            # Even fast programs can exit before the polling loop detects huge output.
            if out_path.stat().st_size > OUTPUT_LIMIT or err_path.stat().st_size > OUTPUT_LIMIT:
                over_output = True
            if timed_out or over_output:
                _terminate_container(cidfile)
            stdout = out_path.read_bytes()[:OUTPUT_LIMIT].decode('utf-8', 'replace')
            stderr = err_path.read_bytes()[:OUTPUT_LIMIT].decode('utf-8', 'replace')
            return {'returncode': proc.returncode, 'stdout': stdout, 'stderr': stderr,
                    'wall_time_ms': round((time.monotonic() - start)*1000, 2),
                    'timed_out': timed_out, 'output_limit': over_output}
        except OSError as exc:
            _terminate_container(cidfile)
            return {'returncode': None, 'stdout': '', 'stderr': str(exc),
                    'wall_time_ms': 0, 'timed_out': False, 'output_limit': False}
        finally:
            # Docker --rm removes completed containers. Force-remove a stalled one.
            _terminate_container(cidfile)


def compile_and_smoke(source_bytes: bytes, sample_input: str,
                      time_limit_s: float = 1, memory_limit_mb: int = 64, run_smoke: bool = True) -> dict:
    """Compile in Docker (384 MB) then run binary in Docker (rubric limits)."""
    if not isinstance(source_bytes, bytes) or len(source_bytes) > 100 * 1024:
        raise ValueError('Invalid C source size.')
    if not isinstance(sample_input, str) or len(sample_input.encode('utf-8')) > 8192:
        raise ValueError('Sample input cannot exceed 8 KiB.')
    if not 0 < time_limit_s <= 30 or not 16 <= memory_limit_mb <= 4096:
        raise ValueError('Unsupported sandbox resource limits.')

    ready, message, image_id = check_environment()
    if not ready:
        return {'compiler': {'status': 'unavailable', 'message': message},
                'smoke': {'status': 'skipped', 'message': 'Docker unavailable.'},
                'image_id': None}

    with tempfile.TemporaryDirectory(prefix='ag_sandbox_') as temp:
        folder = Path(temp)
        src, output_dir = folder / 'student.c', folder / 'out'
        output_dir.mkdir()
        src.write_bytes(source_bytes)
        os.chmod(src, 0o644)
        os.chmod(output_dir, 0o777)  # Only ephemeral directory shared with compiler container.

        compile_id = folder / 'compile.cid'
        compile_cmd = _docker_cmd(memory_mb=384, cidfile=compile_id,
                                   mounts=[*_bind(src, '/src/student.c'), *_bind(output_dir, '/out', False)])
        compile_cmd += ['gcc', '-O2', '-Wall', '-Wextra', '-std=c11',
                        '/src/student.c', '-o', '/out/program']
        compilation = _invoke(compile_cmd, compile_id, wall_timeout=COMPILE_TIMEOUT_S)
        if compilation['timed_out']:
            compile_state = 'timeout'
            compile_message = 'Compilation exceeded its time limit.'
        elif compilation['output_limit']:
            compile_state = 'failed'
            compile_message = 'Compiler generated too much output.'
        elif compilation['returncode'] == 0 and (output_dir / 'program').is_file():
            compile_state = 'passed'
            compile_message = 'Compilation succeeded.'
        else:
            compile_state = 'failed'
            compile_message = 'Compilation failed. Review GCC diagnostics.'
        compiler = {'status': compile_state, 'message': compile_message,
                    'exit_code': compilation['returncode'],
                    'diagnostics': (compilation['stderr'] + '\n' + compilation['stdout']).strip()[:OUTPUT_LIMIT],
                    'warning_count': len(re.findall(r'\bwarning:', compilation['stderr'])),
                    'wall_time_ms': compilation['wall_time_ms']}
        if compile_state != 'passed':
            return {'compiler': compiler,
                    'smoke': {'status': 'skipped', 'message': 'Compile check did not pass.'},
                    'image_id': image_id}

        if not run_smoke:
            return {'compiler': compiler,
                    'smoke': {'status': 'skipped', 'message': 'Teacher has not configured sample input.'},
                    'image_id': image_id}

        # Redirect input inside the container from a read-only file, not a pipe.
        input_path = folder / 'sample_input.txt'
        input_path.write_bytes(sample_input.encode('utf-8'))
        os.chmod(input_path, 0o644)
        smoke_id = folder / 'smoke.cid'
        smoke_cmd = _docker_cmd(memory_mb=memory_limit_mb, cidfile=smoke_id,
                                 mounts=[*_bind(output_dir / 'program', '/app/program'),
                                         *_bind(input_path, '/app/sample.txt')])
        # GNU timeout starts after container startup. This applies the wall-clock
        # limit to the program itself, not the Docker Desktop startup cost.
        smoke_cmd += ['sh', '-c', 'exec timeout -s KILL "$1" /app/program < /app/sample.txt',
                      'sh', f'{time_limit_s}s']
        smoke = _invoke(smoke_cmd, smoke_id, wall_timeout=DOCKER_STARTUP_GRACE_S + time_limit_s + 3)
        if smoke['timed_out']:
            status, explanation = 'timeout', 'Docker/run exceeded the outer wall-clock limit.'
        elif smoke['output_limit']:
            status, explanation = 'output_limit', 'Program produced excessive output.'
        elif smoke['returncode'] == 124:
            status, explanation = 'timeout', 'Program exceeded execution time.'
        elif smoke['returncode'] == 137:
            status, explanation = 'resource_limit', 'Program was killed: possible timeout or memory limit.'
        elif smoke['returncode'] == 0:
            status, explanation = 'passed', 'Program exited normally (output correctness not checked).'
        else:
            status, explanation = 'crashed', 'Program exited abnormally or exceeded a resource limit.'
        return {'compiler': compiler,
                'smoke': {'status': status, 'message': explanation,
                          'exit_code': smoke['returncode'], 'stdout': smoke['stdout'],
                          'stderr': smoke['stderr'], 'wall_time_ms': smoke['wall_time_ms'],
                          'time_limit_s': time_limit_s, 'memory_limit_mb': memory_limit_mb},
                'image_id': image_id}


def run_c_tests(source_bytes: bytes, inputs: list[str], *, time_limit_s: float = 1,
                memory_limit_mb: int = 64) -> dict:
    """Compile once inside Docker and run each input in an isolated container.

    Used for BOTH the instructor reference and student submissions. The program
    never runs directly on the host. All returned outputs are bounded.
    """
    if not isinstance(source_bytes, bytes) or not source_bytes or len(source_bytes) > 100 * 1024:
        raise ValueError('C source must be between 1 and 102400 bytes.')
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 20 or any(
        not isinstance(s, str) or len(s.encode('utf-8')) > 8192 for s in inputs
    ):
        raise ValueError('Use 1 to 20 UTF-8 inputs, each no larger than 8 KiB.')
    if not isinstance(time_limit_s, (float, int)) or not 0 < time_limit_s <= 30:
        raise ValueError('Time limit must be between 0 and 30 seconds.')
    if not isinstance(memory_limit_mb, int) or not 16 <= memory_limit_mb <= 4096:
        raise ValueError('Memory limit must be between 16 and 4096 MB.')
    ready, message, image_id = check_environment()
    if not ready:
        return {'status': 'unavailable', 'message': message, 'image_id': None, 'results': []}
    with tempfile.TemporaryDirectory(prefix='ag_suite_') as temp:
        folder = Path(temp)
        src, out = folder / 'program.c', folder / 'out'
        out.mkdir()
        src.write_bytes(source_bytes)
        os.chmod(src, 0o644)
        os.chmod(out, 0o777)
        cid = folder / 'gcc.cid'
        cmd = _docker_cmd(memory_mb=384, cidfile=cid, mounts=[*_bind(src, '/src/program.c'),
                                                              *_bind(out, '/out', False)])
        cmd += ['gcc', '-O2', '-Wall', '-Wextra', '-std=c11', '/src/program.c', '-o', '/out/program']
        compilation = _invoke(cmd, cid, wall_timeout=COMPILE_TIMEOUT_S)
        if compilation['timed_out'] or compilation['output_limit'] or compilation['returncode'] != 0 or not (out / 'program').is_file():
            return {'status': 'compile_error', 'message': 'Compilation failed or timed out.',
                    'compiler': {'diagnostics': compilation['stderr'][:OUTPUT_LIMIT],
                                 'exit_code': compilation['returncode']},
                    'image_id': image_id, 'results': []}
        results = []
        for i, input_text in enumerate(inputs):
            input_path = folder / f'input_{i}.txt'
            input_path.write_bytes(input_text.encode('utf-8'))
            os.chmod(input_path, 0o644)
            run_cid = folder / f'run_{i}.cid'
            run_cmd = _docker_cmd(memory_mb=memory_limit_mb, cidfile=run_cid,
                                   mounts=[*_bind(out / 'program', '/app/program'),
                                           *_bind(input_path, '/app/input.txt')])
            run_cmd += ['sh', '-c', 'exec timeout -s TERM "$1" /app/program < /app/input.txt',
                        'sh', f'{time_limit_s}s']
            result = _invoke(run_cmd, run_cid, wall_timeout=DOCKER_STARTUP_GRACE_S + time_limit_s + 3)
            if result['timed_out'] or result['returncode'] in (124, 143):
                state = 'timeout'
            elif result['output_limit']:
                state = 'output_limit'
            elif result['returncode'] == 0:
                state = 'passed'
            elif result['returncode'] == 137:
                state = 'resource_limit'
            else:
                state = 'runtime_error'
            results.append({'status': state, 'exit_code': result['returncode'],
                            'stdout': result['stdout'], 'stderr': result['stderr'],
                            'wall_time_ms': result['wall_time_ms']})
        return {'status': 'ok', 'message': 'Completed all execution checks.',
                'image_id': image_id, 'results': results}
