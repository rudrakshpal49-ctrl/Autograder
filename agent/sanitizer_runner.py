"""One Linux GCC sanitizer backend on both hosts.

Linux: directly executes the shared GCC worker.
Windows: calls that SAME worker with WSL2 Ubuntu using wsl.exe.
No Dr. Memory, Windows LLVM, or MinGW sanitizer runtime is involved.
CAUTION: native Linux and WSL2 are not secure sandboxes for hostile C programs.
"""
from __future__ import annotations

import base64
import json
import os
from pathlib import Path
import shutil
import subprocess

from agent.sanitizer_worker import execute

DEFAULT_FLAGS = '-fsanitize=address,undefined'
ALLOWED_FLAGS = {DEFAULT_FLAGS, '-fsanitize=address', '-fsanitize=undefined'}


def _is_windows() -> bool:
    return os.name == 'nt'


def find_sanitizer_compiler(platform=None):
    """GCC runs in Linux; Windows system GCC is never used here."""
    platform = os.name if platform is None else platform
    return None if platform == 'nt' else shutil.which('gcc')


def _wsl_prefix() -> list[str] | None:
    wsl = shutil.which('wsl.exe')
    if not wsl:
        return None
    cmd = [wsl]
    distro = os.environ.get('AG_WSL_DISTRO', '').strip()
    if distro:
        cmd += ['--distribution', distro]
    return cmd + ['--exec']


def _via_wsl(payload: dict) -> dict:
    command = _wsl_prefix()
    if command is None:
        return {'status': 'unavailable', 'runs': [],
                'reason': 'WSL2 not found. Install WSL2 and Ubuntu (wsl --install), then restart.'}
    worker_path = str(Path(__file__).with_name('sanitizer_worker.py').resolve())
    try:
        mapped = subprocess.run(command + ['wslpath', '-u', worker_path],
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, timeout=12, check=False)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {'status': 'unavailable', 'runs': [],
                'reason': f'Cannot start WSL2: {exc}'[:650]}
    linux_worker = mapped.stdout.strip()
    if mapped.returncode != 0 or not linux_worker.startswith('/') or '\n' in linux_worker:
        return {'status': 'unavailable', 'runs': [], 'reason':
                'WSL2 cannot access the AutoGrader project directory. Run the project '
                'from a local Windows drive or run Streamlit inside WSL2. ' +
                mapped.stderr[:450]}
    wall = min(90, max(3, payload['time_limit_s'] * 3))
    timeout = min(1200, 65 + wall * len(payload['inputs']))
    try:
        proc = subprocess.run(command + ['python3', linux_worker],
                              input=json.dumps(payload), capture_output=True,
                              text=True, timeout=timeout, check=False)
    except subprocess.TimeoutExpired:
        return {'status': 'unavailable', 'runs': [],
                'reason': 'WSL2 worker exceeded overall timeout. Linux jobs may need manual cleanup.'}
    except OSError as exc:
        return {'status': 'unavailable', 'runs': [],
                'reason': f'WSL2 worker failed to start: {exc}'[:650]}
    if proc.returncode != 0:
        return {'status': 'unavailable', 'runs': [],
                'reason': 'WSL2 Python worker failed: ' + proc.stderr[:750]}
    try:
        result = json.loads(proc.stdout)
        if not isinstance(result, dict):
            raise ValueError('Not a JSON object')
    except (json.JSONDecodeError, ValueError):
        return {'status': 'unavailable', 'runs': [],
                'reason': 'Invalid WSL2 worker result: ' + (proc.stdout or proc.stderr)[:600]}
    result['backend'] = 'wsl2_gcc'
    result['memory_tool'] = 'WSL2 Ubuntu GCC'
    return result


def run_sanitizer_cases(source: bytes, inputs: list[str], *, flags=DEFAULT_FLAGS,
                        time_limit_s=1, memory_limit_mb=64) -> dict:
    """Validate the same request regardless of Python host OS."""
    if flags not in ALLOWED_FLAGS:
        raise ValueError('Unsupported sanitizer flags.')
    if not isinstance(source, bytes) or not 0 < len(source) <= 102400:
        raise ValueError('Invalid C source size.')
    if not isinstance(inputs, list) or not 1 <= len(inputs) <= 60:
        raise ValueError('Expected 1-60 approved tests.')
    if any(not isinstance(s, str) or len(s.encode('utf-8')) > 8192 for s in inputs):
        raise ValueError('Each stdin must be text up to 8192 bytes.')
    if (not isinstance(time_limit_s, (int, float)) or isinstance(time_limit_s, bool)
            or not 0 < time_limit_s <= 30 or not isinstance(memory_limit_mb, int)
            or isinstance(memory_limit_mb, bool) or not 16 <= memory_limit_mb <= 4096):
        raise ValueError('Invalid resource limits.')
    payload = {'source_b64': base64.b64encode(source).decode('ascii'),
               'inputs': inputs, 'flags': flags, 'time_limit_s': time_limit_s,
               'memory_limit_mb': memory_limit_mb}
    if _is_windows():
        result = _via_wsl(payload)
        result.setdefault('backend', 'wsl2_gcc')
        result.setdefault('tool', 'asan_ubsan')
        return result
    result = execute(payload)
    result['backend'] = 'linux_gcc'
    return result
