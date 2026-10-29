"""Deterministic grading of the existing sandbox GCC compile check."""
from __future__ import annotations

import re

SANDBOX_FLAGS = {'-O2', '-Wall', '-Wextra', '-std=c11'}
SUPPORTED_EXTRA = {'-Werror'}


def evaluate_syntax(compiler: dict | None, params: dict | None = None) -> dict:
    params = params or {}
    if not isinstance(params, dict):
        raise ValueError('Syntax params must be an object.')
    flags = params.get('flags', '-Wall -Wextra -std=c11')
    if not isinstance(flags, str) or not set(flags.split()).issubset(SANDBOX_FLAGS | SUPPORTED_EXTRA):
        return {'status': 'unavailable', 'score': None,
                'feedback': 'Rubric requests compiler flags not used by the Docker compile check.',
                'evidence': {'actual_flags': sorted(SANDBOX_FLAGS), 'requested_flags': flags}}
    penalty = params.get('penalty_per_warning', 0.1)
    if isinstance(penalty, bool) or not isinstance(penalty, (int, float)) or not 0 <= penalty <= 1:
        raise ValueError('penalty_per_warning must be between 0 and 1.')
    if not compiler or compiler.get('status') in (None, 'unavailable', 'skipped', 'timeout'):
        return {'status': 'unavailable', 'score': None,
                'feedback': 'Compiler result unavailable; check Docker and rerun First Pass.',
                'evidence': {'compiler_status': (compiler or {}).get('status')}}
    diagnostics = str(compiler.get('diagnostics', ''))
    warnings = int(compiler.get('warning_count', len(re.findall(r'\bwarning:', diagnostics))))
    errors = len(re.findall(r'\berror:', diagnostics))
    if compiler.get('status') != 'passed' or ('-Werror' in flags.split() and warnings > 0):
        return {'status': 'failed', 'score': 0.0,
                'feedback': ('Compilation would fail under -Werror due to GCC warnings.'
                             if compiler.get('status') == 'passed' else
                             'Compilation failed; see compiler diagnostics.'),
                'evidence': {'warnings': warnings, 'errors': errors, 'diagnostics': diagnostics[:8192]}}
    score = round(max(0.0, 1.0 - penalty * warnings), 4)
    return {'status': 'passed', 'score': score,
            'feedback': f'Compilation succeeded with {warnings} GCC warning(s).',
            'evidence': {'warnings': warnings, 'errors': errors, 'diagnostics': diagnostics[:8192],
                         'warning_penalty': penalty, 'werror_requested': '-Werror' in flags.split()}}
