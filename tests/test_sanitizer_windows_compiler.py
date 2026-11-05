"""Windows WSL2 bridge tests (supersedes the removed Dr. Memory tests)."""
import json
from types import SimpleNamespace

import pytest

from agent import sanitizer_runner
from agent.analyzers.sanitizer_run import grade_memory_safety


def _cases():
    return [{'id': 1, 'title': 'normal', 'category': 'normal', 'stdin': '1\n'}]


def test_windows_uses_wsl_not_old_windows_toolchains(monkeypatch):
    monkeypatch.setattr(sanitizer_runner, '_is_windows', lambda: True)
    monkeypatch.setattr(sanitizer_runner, '_via_wsl', lambda payload: {
        'status': 'completed', 'runs': [{'status': 'ok', 'stderr': ''}],
        'backend': 'wsl2_gcc', 'tool': 'asan_ubsan', 'flags': payload['flags']})
    r = sanitizer_runner.run_sanitizer_cases(b'int main(){return 0;}', ['1\n'])
    assert r['backend'] == 'wsl2_gcc'
    assert r['tool'] == 'asan_ubsan'
    assert sanitizer_runner.find_sanitizer_compiler(platform='nt') is None


def test_windows_wsl_not_installed(monkeypatch):
    monkeypatch.setattr(sanitizer_runner, '_is_windows', lambda: True)
    monkeypatch.setattr(sanitizer_runner.shutil, 'which', lambda name: None)
    r = sanitizer_runner.run_sanitizer_cases(b'int main(){return 0;}', ['1\n'])
    assert r['status'] == 'unavailable'
    assert 'WSL2' in r['reason']


def test_wsl_path_conversion_and_worker_protocol(monkeypatch):
    calls = []
    monkeypatch.setattr(sanitizer_runner, '_is_windows', lambda: True)
    monkeypatch.setenv('AG_WSL_DISTRO', 'Ubuntu')
    monkeypatch.setattr(sanitizer_runner.shutil, 'which', lambda name: r'C:\Windows\System32\wsl.exe')

    def fake_run(command, **kw):
        calls.append((command, kw))
        if 'wslpath' in command:
            return SimpleNamespace(returncode=0, stdout='/mnt/c/AutoGrader/agent/sanitizer_worker.py\n', stderr='')
        request = json.loads(kw['input'])
        assert request['inputs'] == ['1\n']
        assert request['flags'] == '-fsanitize=address,undefined'
        response = {'status': 'completed', 'tool': 'asan_ubsan', 'runs': [{'status': 'ok', 'stderr': ''}]}
        return SimpleNamespace(returncode=0, stdout=json.dumps(response), stderr='')

    monkeypatch.setattr(sanitizer_runner.subprocess, 'run', fake_run)
    r = sanitizer_runner.run_sanitizer_cases(b'int main(){return 0;}', ['1\n'])
    assert r['status'] == 'completed' and r['backend'] == 'wsl2_gcc'
    assert calls[0][0][1:3] == ['--distribution', 'Ubuntu']
    assert calls[1][0][-2:] == ['python3', '/mnt/c/AutoGrader/agent/sanitizer_worker.py']
    assert all(not x[1].get('shell', False) for x in calls)


def test_wsl_missing_distro_or_unmapped_project_is_unavailable(monkeypatch):
    monkeypatch.setattr(sanitizer_runner.shutil, 'which', lambda name: 'wsl.exe')
    monkeypatch.setattr(sanitizer_runner.subprocess, 'run', lambda *args, **kw:
                        SimpleNamespace(returncode=1, stdout='', stderr='no distribution'))
    r = sanitizer_runner._via_wsl({'flags': sanitizer_runner.DEFAULT_FLAGS,
                                   'time_limit_s': 1, 'inputs': ['1\n']})
    assert r['status'] == 'unavailable' and 'WSL2' in r['reason']


def test_wsl_invalid_worker_json_never_gets_grade(monkeypatch):
    monkeypatch.setattr(sanitizer_runner, '_is_windows', lambda: True)
    monkeypatch.setattr(sanitizer_runner.shutil, 'which', lambda name: 'wsl.exe')
    count = 0
    def fake_run(*args, **kw):
        nonlocal count
        count += 1
        return (SimpleNamespace(returncode=0, stdout='/mnt/c/runner.py\n', stderr='')
                if count == 1 else SimpleNamespace(returncode=0, stdout='oops', stderr=''))
    monkeypatch.setattr(sanitizer_runner.subprocess, 'run', fake_run)
    r = grade_memory_safety(b'int main(){return 0;}', _cases(), time_limit_s=1, memory_limit_mb=64)
    assert r['status'] == 'unavailable' and r['score'] is None


def test_simulated_wsl_memory_error_is_scored(monkeypatch):
    monkeypatch.setattr(sanitizer_runner, '_is_windows', lambda: True)
    monkeypatch.setattr(sanitizer_runner, '_via_wsl', lambda payload: {
        'status': 'completed', 'backend': 'wsl2_gcc', 'tool': 'asan_ubsan',
        'runs': [{'status': 'nonzero_exit', 'stderr':
                  'ERROR: AddressSanitizer: heap-buffer-overflow\n#0 main student.c:5:1'}]})
    r = grade_memory_safety(b'int main(){return 0;}', _cases(), time_limit_s=1, memory_limit_mb=64)
    assert r['status'] == 'completed' and r['score'] == .5
    assert r['evidence']['backend'] == 'wsl2_gcc'


def test_nonzero_without_diagnostics_does_not_count_as_clean(monkeypatch):
    monkeypatch.setattr(sanitizer_runner, '_is_windows', lambda: True)
    monkeypatch.setattr(sanitizer_runner, '_via_wsl', lambda payload: {
        'status': 'completed', 'backend': 'wsl2_gcc', 'tool': 'asan_ubsan',
        'runs': [{'status': 'nonzero_exit', 'stderr': ''}]})
    r = grade_memory_safety(b'int main(){return 0;}', _cases(), time_limit_s=1, memory_limit_mb=64)
    assert r['status'] == 'incomplete' and r['score'] is None


@pytest.mark.parametrize('invalid', ['-fsanitize=thread', '-fplugin=bad'])
def test_reject_arbitrary_sanitizer_flags(invalid):
    with pytest.raises(ValueError):
        sanitizer_runner.run_sanitizer_cases(b'int main(){return 0;}', ['1\n'], flags=invalid)
