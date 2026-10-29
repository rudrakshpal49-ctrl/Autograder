"""Metrics for C functions recognized by Lizard; includes non-comment NLOC.

The metrics are heuristics rather than a full C syntax/semantic analysis.
"""
from __future__ import annotations


def _without_c_comments(source: str) -> str:
    """Remove comments but preserve line breaks, strings, and character constants."""
    result = []
    state = 'code'
    i = 0
    while i < len(source):
        ch = source[i]
        nxt = source[i + 1] if i + 1 < len(source) else ''
        if state == 'code':
            if ch == '/' and nxt == '/':
                result.extend([' ', ' ']); i += 2; state = 'line'; continue
            if ch == '/' and nxt == '*':
                result.extend([' ', ' ']); i += 2; state = 'block'; continue
            result.append(ch)
            if ch == '"': state = 'string'
            elif ch == "'": state = 'char'
        elif state in ('string', 'char'):
            result.append(ch)
            if ch == '\\' and nxt:
                result.append(nxt); i += 2; continue
            if ch == ('"' if state == 'string' else "'"):
                state = 'code'
        elif state == 'line':
            if ch == '\n': result.append('\n'); state = 'code'
            else: result.append(' ')
        elif state == 'block':
            if ch == '*' and nxt == '/':
                result.extend([' ', ' ']); i += 2; state = 'code'; continue
            if ch == '\n': result.append('\n')
            else: result.append(' ')
        i += 1
    return ''.join(result)


def analyze_functions(source: str) -> dict:
    if not isinstance(source, str) or not source.strip():
        return {'status': 'unavailable', 'message': 'C source is empty.', 'functions': []}
    try:
        import lizard
    except ImportError:
        return {'status': 'unavailable', 'message': 'Install the lizard Python package.', 'functions': []}
    try:
        info = lizard.analyze_file.analyze_source_code('student.c', source)
        lines = _without_c_comments(source).splitlines()
        functions = []
        for f in info.function_list:
            start, end = int(f.start_line), int(f.end_line)
            nloc = sum(bool(line.strip()) for line in lines[max(0, start - 1):end])
            functions.append({
                'name': str(f.name),
                'cyclomatic_complexity': int(f.cyclomatic_complexity),
                'length': int(f.length),
                'nloc': nloc,
                'start_line': start, 'end_line': end,
            })
    except Exception:
        return {'status': 'unavailable', 'message': 'Could not analyze C functions.', 'functions': []}
    if not functions:
        return {'status': 'unavailable', 'message': 'No C function definitions recognized.', 'functions': []}
    return {'status': 'ok', 'functions': functions}
