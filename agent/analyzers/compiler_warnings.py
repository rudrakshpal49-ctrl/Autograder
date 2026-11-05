"""Score C syntax using GCC diagnostics already collected by First Pass.

This analyzer does NOT execute student code or call GCC again. It uses the
existing native GCC compilation (-O2 -Wall -Wextra -std=c11).
"""

import math


def analyze_compiler_warnings(context: dict, params: dict) -> dict:
    """Return a 0..1 syntax score and clear evidence of compiler problems.

    context: {'first_pass': the result from evaluate_source(...)}
    params: {
        'max_warnings': 0,             # Allowed without a penalty
        'penalty_per_warning': 0.1    # Deduction for each excess warning
    }
    """
    max_warnings = params.get('max_warnings', 0)
    penalty = params.get('penalty_per_warning', 0.1)

    if isinstance(max_warnings, bool) or not isinstance(max_warnings, int) or max_warnings < 0:
        raise ValueError('max_warnings must be a non-negative integer.')
    if (isinstance(penalty, bool) or not isinstance(penalty, (int, float))
            or not math.isfinite(penalty) or not 0 <= penalty <= 1):
        raise ValueError('penalty_per_warning must be between 0 and 1.')
    # Legacy instructor rules may include custom GCC flags. First Pass currently
    # uses fixed flags, so do not reject the entire evaluation because of them.
    custom_flags_ignored = 'flags' in params

    compiler = context['first_pass']['compiler']
    status = compiler['status']
    diagnostics = compiler.get('diagnostics', '')

    # Collect just the warning/error messages for a concise student report.
    problems = [line.strip() for line in diagnostics.splitlines()
                if 'warning:' in line or 'error:' in line or 'fatal error:' in line]
    warnings = compiler.get('warning_count')
    if warnings is None:
        warnings = sum('warning:' in line for line in diagnostics.splitlines())

    if status == 'passed':
        deductions = max(0, warnings - max_warnings)
        score = round(max(0.0, 1.0 - deductions * penalty), 4)
        feedback = ('Compiled without warnings.' if warnings == 0 else
                    f'Compilation succeeded with {warnings} warning(s).')
    elif status == 'failed':
        score = 0.0
        feedback = 'Compilation failed. Fix the GCC errors before submitting.'
    else:
        # An unavailable/incomplete compile is not evidence of bad student syntax.
        raise ValueError('Syntax can only be scored after a completed compile check.')

    if custom_flags_ignored:
        feedback += ' Custom GCC flags were ignored; First Pass uses fixed GCC flags.'

    return {
        'score': score,
        'evidence': {
            'compiled': status == 'passed',
            'warning_count': warnings,
            'diagnostics': problems[:10],
        },
        'feedback': feedback,
    }
