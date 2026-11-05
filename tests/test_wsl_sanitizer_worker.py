"""Real GCC checks via the same worker used inside WSL2."""
import json
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

from agent.sanitizer_runner import run_sanitizer_cases
from agent.analyzers.sanitizer_run import grade_memory_safety

pytestmark = pytest.mark.skipif(not shutil.which('gcc') or sys.platform == 'win32',
                                reason='Needs Linux GCC sanitizer libraries')


def _case():
    return [{'id': 1, 'title': 'normal', 'category': 'normal', 'stdin': '1\n'}]


def test_real_linux_clean():
    r = grade_memory_safety(b'#include <stdio.h>\nint main(void){puts("YES");return 0;}\n',
                            _case(), time_limit_s=1, memory_limit_mb=64)
    assert r['status'] == 'completed' and r['score'] == 1
    assert r['evidence']['backend'] == 'linux_gcc'


def test_real_linux_out_of_bounds():
    source = (b'#include <stdio.h>\nint main(void){volatile int a[2]={1,2};'
              b'volatile int i=4; printf("%d\\n",a[i]);return 0;}\n')
    r = grade_memory_safety(source, _case(), time_limit_s=1, memory_limit_mb=64)
    assert r['status'] == 'completed', r['feedback']
    assert r['score'] <= .5
    assert any(f['type'] == 'memory_error' for f in r['evidence']['findings'])


def test_worker_json_protocol():
    from agent import sanitizer_worker
    p = Path(sanitizer_worker.__file__)
    payload = {'source_b64': 'aW50IG1haW4oKXtyZXR1cm4gMDt9',
               'inputs': ['1\n'], 'time_limit_s': 1,
               'memory_limit_mb': 64, 'flags': '-fsanitize=undefined'}
    r = subprocess.run([sys.executable, str(p)], input=json.dumps(payload),
                       capture_output=True, text=True, timeout=45)
    result = json.loads(r.stdout)
    assert r.returncode == 0
    assert result['status'] == 'completed' and result['runs'][0]['status'] == 'ok'
