"""Small live GCC checks on safe, author-controlled C samples only."""
import shutil

import pytest

from agent.sandbox import check_environment, compile_and_smoke
from agent.first_pass import evaluate_source


@pytest.mark.skipif(shutil.which('gcc') is None, reason='native GCC unavailable')
def test_local_gcc_success():
    source = (b'#include <stdio.h>\nint main(void) { int a,b; '
              b'if(scanf("%d %d", &a, &b)!=2) return 1; '
              b'printf("%d\\n", a+b); return 0; }\n')
    outcome = evaluate_source(source, sample_input='5 7\n', time_limit_s=2)
    assert outcome['compiler']['status'] == 'passed'
    assert outcome['smoke']['status'] == 'passed'
    assert outcome['smoke']['stdout'].replace('\r\n', '\n') == '12\n'
    assert outcome['status'] == 'ready'
    assert outcome['execution_backend'] == 'native_gcc'


@pytest.mark.skipif(shutil.which('gcc') is None, reason='native GCC unavailable')
def test_local_gcc_timeout():
    source = b'int main(void) { volatile int x=0; for (;;) { x++; } }\n'
    outcome = evaluate_source(source, sample_input='', time_limit_s=0.25)
    assert outcome['compiler']['status'] == 'passed'
    assert outcome['smoke']['status'] == 'timeout'
    assert outcome['status'] == 'blocked'


@pytest.mark.skipif(shutil.which('gcc') is None, reason='native GCC unavailable')
def test_local_gcc_compile_error():
    outcome = compile_and_smoke(b'int main(void) { syntax error }', '')
    assert outcome['compiler']['status'] == 'failed'
    assert outcome['smoke']['status'] == 'skipped'


def test_gcc_environment_matches_available_compiler():
    available, message, version = check_environment()
    if shutil.which('gcc'):
        assert available and version and 'GCC' in message
    else:
        assert not available and version is None
