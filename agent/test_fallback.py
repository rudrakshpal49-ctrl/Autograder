"""Conservative, deterministic fallback inputs for *recognized* C assignment formats.

These are candidate stdin strings only. A teacher-provided reference must validate
and produce stdout before any candidate may become an approved grading test.
Never invent a format for an unrecognized problem.
"""
from __future__ import annotations

import re
from agent.test_generation import _normalize_stdin


def _parentheses_problem(problem: str, sample: str, oracle: str) -> bool:
    description = problem.lower()
    # This generator specifically targets the T<=50, length<=1000 format.
    # We must not assume these limits for other parentheses assignments.
    if not (re.search(r'\b50\b', description) and re.search(r'\b1000\b', description)):
        return False
    text = sample.replace('\r', '').strip().split('\n')
    if not (('parenthes' in description or 'bracket' in description)
            and ('balanc' in description or 'valid' in description)
            and len(text) >= 2 and text[0].strip().isdigit()):
        return False
    if int(text[0].strip()) != len(text) - 1:
        return False
    if any(not line or set(line) - {'(', ')'} for line in text[1:]):
        return False
    # Check the reference matches the sequence-based per-testcase input format.
    return ('scanf' in oracle and ('%s' in oracle or re.search(r'%[0-9]+s', oracle) is not None))


def _parens_normal(index: int) -> str:
    # No min/max boundaries: varied ordinary T, depths, lengths and balancedness.
    t = 3 + index % 6
    sequences = []
    for j in range(t):
        depth = 2 + ((index * 7 + j * 3) % 12)
        kind = (index * 3 + j) % 6
        if kind == 0:
            s = '(' * depth + ')' * depth
        elif kind == 1:
            s = '()' * depth
        elif kind == 2:
            s = '(' * depth + ')' * (depth - 1)
        elif kind == 3:
            s = ')(' * depth
        elif kind == 4:
            s = '(' * depth + ')' * depth + '()' * (j + 1)
        else:
            s = '()' * (depth - 1) + ')('
        sequences.append(s)
    return str(t) + '\n' + '\n'.join(sequences) + '\n'


def _parens_edge(index: int) -> str:
    # Genuinely different boundaries: shortest length, bad initial prefix,
    # maximum sequence length, maximum count, extreme nesting, odd length.
    kind = index % 5
    if kind == 0:
        return '2\n(\n)\n'
    if kind == 1:
        return '2\n)(\n(()\n'
    if kind == 2:
        return '1\n' + '(' * 500 + ')' * 500 + '\n'
    if kind == 3:
        return '50\n' + '()\n' * 50
    return '2\n' + '(' * 499 + ')' * 499 + '\n' + '(' * 500 + '\n'


def _two_int_sum(problem: str, sample: str, oracle: str) -> bool:
    description = problem.lower()
    tokens = sample.split()
    if not (len(tokens) == 2 and all(re.fullmatch(r'-?\d+', x) for x in tokens)):
        return False
    if any(word in description for word in ('positive integers', 'nonnegative', 'non-negative')):
        return False
    if not ('sum' in description or 'add two' in description or 'addition' in description):
        return False
    return 'scanf' in oracle and '+' in oracle and 'printf' in oracle


def generate_fallback_cases(problem: str, sample_stdin: str, oracle_code: str,
                            category: str, count: int, existing_inputs: list[str],
                            requested_edge_types: list[str] | None = None) -> list[dict]:
    """Produce distinct candidates for recognized formats only.

    This returns no invented cases for unrecognized formats, and never claims
    that a successful reference execution proves all stated preconditions.
    """
    if not oracle_code.strip() or category not in {'normal', 'edge'} or count <= 0:
        return []
    used = {_normalize_stdin(s) for s in existing_inputs}
    results: list[dict] = []
    is_parens = _parentheses_problem(problem, sample_stdin, oracle_code)
    is_sum = _two_int_sum(problem, sample_stdin, oracle_code)
    if not is_parens and not is_sum:
        return []
    requested = list(requested_edge_types or []) if category == 'edge' else []
    # Do not manufacture "negative" or "overflow" cases for parentheses-only stdin.
    parentheses_tags = ['minimum', 'early_closing', 'maximum', 'large_input', 'unbalanced']
    sum_tags = ['zero', 'negatives', 'zero', 'negatives', 'boundary']
    for index in range(100):
        if len(results) >= count:
            break
        if is_parens:
            # Five truly distinct edge scenarios, not re-labeled normal inputs.
            if category == 'edge' and index >= 5:
                break
            if category == 'edge' and requested and parentheses_tags[index] not in requested:
                continue
            stdin = _parens_normal(index) if category == 'normal' else _parens_edge(index)
            label = f'Parentheses {"regular variation" if category == "normal" else "boundary scenario"} {index + 1}'
        else:
            if category == 'normal':
                a, b = 5 + index * 13, 11 + (index * 17) % 200
            else:
                pairs = [(0, 0), (-1, 1), (0, 123), (-99, -1), (999, -999)]
                if index >= len(pairs):
                    break
                if requested and sum_tags[index] not in requested:
                    continue
                a, b = pairs[index]
            stdin = f'{a} {b}\n'
            label = f'Addition {category} variation {index + 1}'
        stdin = _normalize_stdin(stdin)
        if stdin in used or len(stdin.encode('utf-8')) > 8192:
            continue
        used.add(stdin)
        edge_type = (parentheses_tags[index] if is_parens else sum_tags[index]) if category == 'edge' else None
        results.append({'stdin': stdin, 'expected_output': '',
                        'title': label, 'category': category, 'edge_type': edge_type,
                        'rationale': ('Deterministic problem-specific scenario, '
                                      'to be checked against instructor reference.')})
    return results
