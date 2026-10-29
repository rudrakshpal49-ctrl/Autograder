"""Conservative, explanatory source checks (not a complete C parser)."""
import re

# These checks are only advisory except existence of main(). Some correct
# C programs use command-line arguments or do not need I/O / explicit return.
READERS = ('scanf', 'fscanf', 'sscanf', 'getchar', 'getc', 'fgets', 'fread', 'read')
WRITERS = ('printf', 'fprintf', 'puts', 'putchar', 'fputs', 'fwrite', 'write')


def mask_literals_and_comments(source: str) -> str:
    """Replace comments/strings/character literals with spaces, keeping positions."""
    output = list(source)
    i, n = 0, len(source)
    while i < n:
        if source.startswith('//', i):
            start = i
            i = source.find('\n', i)
            if i < 0:
                i = n
        elif source.startswith('/*', i):
            start = i
            close = source.find('*/', i + 2)
            i = n if close < 0 else close + 2
        elif source[i] in ('"', "'"):
            start = i
            quote = source[i]
            i += 1
            while i < n:
                if source[i] == '\\':
                    i += 2
                elif source[i] == quote:
                    i += 1
                    break
                else:
                    i += 1
        else:
            i += 1
            continue
        for j in range(start, min(i, n)):
            if output[j] != '\n':
                output[j] = ' '
    return ''.join(output)


def analyze_static_sanity(source: str) -> dict:
    clean = mask_literals_and_comments(source)
    match = re.search(r'\bmain\s*\([^;{}]*\)\s*\{', clean)
    main_present = match is not None
    body = ''
    if match:
        start = match.end() - 1
        depth = 0
        for i in range(start, len(clean)):
            if clean[i] == '{':
                depth += 1
            elif clean[i] == '}':
                depth -= 1
                if depth == 0:
                    body = clean[start + 1:i]
                    break
    def has_call(names, text):
        return any(re.search(r'\b' + name + r'\s*\(', text) for name in names)

    checks = {
        'main': main_present,
        'input_reader': has_call(READERS, clean),
        'output_writer': has_call(WRITERS, clean),
        'return_in_main': bool(re.search(r'\breturn\b', body)),
    }
    notes = []
    if not checks['main']:
        notes.append('No main() function definition detected.')
    if not checks['input_reader']:
        notes.append('No recognized input function detected; this can be valid for some problems.')
    if not checks['output_writer']:
        notes.append('No recognized output function detected; this can be valid for some problems.')
    if not checks['return_in_main']:
        notes.append('No explicit return in main(); falling off main() is legal in C11.')
    return {
        'status': 'failed' if not main_present else ('warning' if notes else 'passed'),
        'checks': checks,
        'notes': notes,
        'message': 'All four advisory checks detected.' if not notes else ' '.join(notes),
    }
