"""Smoke-check the selected GCC sanitizer backend with a trusted tiny C program.

Usage from the AutoGrader project directory:
  python tools/check_memory_backend.py
"""
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from agent.sanitizer_runner import run_sanitizer_cases

SOURCE = b'int main(void) { return 0; }\n'


def main():
    result = run_sanitizer_cases(SOURCE, ['\n'], time_limit_s=1, memory_limit_mb=64)
    print('Backend:', result.get('backend', 'unavailable'))
    print('Status:', result.get('status', 'unknown'))
    if result.get('compiler'):
        print('GCC:', result['compiler'])
    if result.get('reason'):
        print('Reason:', result['reason'])
    runs = result.get('runs', [])
    if runs:
        print('Test:', runs[0].get('status'))
        if runs[0].get('stderr'):
            print('Diagnostics:', runs[0]['stderr'][:800])
    return 0 if result.get('status') == 'completed' and len(runs) == 1 and runs[0]['status'] == 'ok' else 1


if __name__ == '__main__':
    sys.exit(main())
