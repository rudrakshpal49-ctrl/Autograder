"""Source-only C structure analysis with Lizard; optional Cppcheck diagnostics.

The *grade* is based only on Lizard's function complexity and physical
length, so installing Cppcheck does not unexpectedly change the grade.
Cppcheck findings are included as additional evidence, not as proof of errors.
Nothing here compiles or executes the student program.
"""

import math
import shutil
import subprocess
import tempfile
from pathlib import Path
import xml.etree.ElementTree as ET


def _functions_from_lizard(source: str) -> list[dict]:
    try:
        import lizard
    except ImportError as exc:
        raise RuntimeError('Lizard is not installed. Run: python -m pip install -r requirements.txt') from exc

    # The .c suffix tells Lizard to use its C/C++ parser.
    analysis = lizard.analyze_file.analyze_source_code('submission.c', source)
    functions = []
    for func in analysis.function_list:
        start = int(func.start_line)
        end = int(func.end_line)
        functions.append({
            'name': str(func.name),
            'start_line': start,
            'end_line': end,
            'length': end - start + 1,  # Physical lines, including blanks/comments.
            'cyclomatic': int(func.cyclomatic_complexity),
        })
    return functions


def _cppcheck_diagnostics(source: str) -> dict:
    """Return bounded diagnostics; missing Cppcheck never affects grade."""
    exe = shutil.which('cppcheck')
    if exe is None:
        return {'status': 'not_installed', 'issues': [],
                'message': 'Cppcheck not found on PATH; additional static diagnostics not checked.'}

    with tempfile.TemporaryDirectory(prefix='autograder_structure_') as folder:
        file_path = Path(folder) / 'submission.c'
        file_path.write_text(source, encoding='utf-8')
        try:
            completed = subprocess.run(
                [exe, '--quiet', '--xml', '--xml-version=2',
                 '--enable=warning,style,performance,portability', '--std=c11',
                 str(file_path)],
                capture_output=True, text=True, encoding='utf-8', errors='replace',
                timeout=10, check=False,
            )
        except (OSError, subprocess.TimeoutExpired):
            return {'status': 'unavailable', 'issues': [],
                    'message': 'Cppcheck could not complete its source scan.'}

    try:
        root = ET.fromstring(completed.stderr)
        errors = root.find('errors')
        if root.tag != 'results' or errors is None:
            raise ET.ParseError('Invalid Cppcheck XML format')
        issues = []
        all_issues = errors.findall('error')
        for issue in all_issues[:20]:
            location = issue.find('location')
            issues.append({
                'severity': issue.get('severity', 'unknown'),
                'id': issue.get('id', 'unknown'),
                'message': issue.get('msg', ''),
                'line': int(location.get('line', '0')) if location is not None else 0,
            })
    except (ET.ParseError, ValueError):
        return {'status': 'unavailable', 'issues': [],
                'message': 'Cppcheck produced an unreadable diagnostics report.'}

    return {'status': 'completed', 'issues': issues,
            'message': f'Cppcheck reported {len(all_issues)} finding(s) (up to 20 shown).'}


def analyze_structure(context: dict, params: dict) -> dict:
    """Score source structure from Lizard function metrics.

    params: max_cyclomatic (default 8), max_function_length (default 40),
    penalty_per_violation (default 0.1). Each function exceeding each limit
    counts as one violation. Score is clamped to [0, 1].
    """
    max_ccn = params.get('max_cyclomatic', 8)
    max_length = params.get('max_function_length', 40)
    penalty = params.get('penalty_per_violation', 0.1)

    for key, value in (('max_cyclomatic', max_ccn),
                       ('max_function_length', max_length)):
        if isinstance(value, bool) or not isinstance(value, int) or value < 1:
            raise ValueError(f'{key} must be a positive integer.')
    if (isinstance(penalty, bool) or not isinstance(penalty, (int, float))
            or not math.isfinite(penalty) or not 0 <= penalty <= 1):
        raise ValueError('penalty_per_violation must be between 0 and 1.')

    source = context['source']
    if isinstance(source, bytes):
        source = source.decode('utf-8')
    if not isinstance(source, str):
        raise ValueError('Structure analyzer requires C source text.')

    try:
        functions = _functions_from_lizard(source)
    except RuntimeError as exc:
        return {'status': 'unavailable', 'score': None,
                'evidence': {'functions': [], 'cppcheck': {'status': 'not_checked', 'issues': []}},
                'feedback': str(exc)}

    # No recognized functions in empty/invalid C should not receive full marks.
    violations = []
    for func in functions:
        if func['cyclomatic'] > max_ccn:
            violations.append(f"{func['name']}: complexity {func['cyclomatic']} exceeds {max_ccn}")
        if func['length'] > max_length:
            violations.append(f"{func['name']}: length {func['length']} exceeds {max_length} lines")
    score = (round(max(0.0, 1.0 - len(violations) * penalty), 4)
             if functions else 0.0)
    cppcheck = _cppcheck_diagnostics(source)
    if not functions:
        feedback = 'No function definitions recognized by Lizard; structure score is 0.'
    elif violations:
        feedback = f'{len(violations)} structural threshold violation(s). ' + '; '.join(violations[:5])
    else:
        feedback = 'All detected functions meet the configured complexity and length limits.'
    if cppcheck['status'] != 'completed':
        feedback += ' ' + cppcheck['message']

    return {
        'status': 'completed',
        'score': score,
        'evidence': {
            'functions': functions[:100],
            'function_count': len(functions),
            'max_cyclomatic_found': max((f['cyclomatic'] for f in functions), default=0),
            'max_function_length_found': max((f['length'] for f in functions), default=0),
            'max_cyclomatic_allowed': max_ccn,
            'max_function_length_allowed': max_length,
            'violations': violations[:30],
            'violation_count': len(violations),
            'cppcheck': cppcheck,
        },
        'feedback': feedback,
    }
