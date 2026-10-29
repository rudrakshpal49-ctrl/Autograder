"""Conservative, problem-independent validation of generated stdin candidates.

Only enforce formats that are stated explicitly in the assignment text.  A
format that cannot be inferred is REVIEW, not VALID.  Trusted expected outputs
come from an optional reference program or a deliberately small checker list.
This module does not execute model-supplied code.
"""
from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass(frozen=True)
class Validation:
    status: str  # 'valid', 'invalid', 'review'
    reason: str = ''
    verified_output: str | None = None


def _mentions_two_counted_arrays(text: str) -> bool:
    """Recognize explicitly described n/A then m/B input (not a task name).

    This is one reusable input shape, not a validator for common-elements
    answers or a general parser for arbitrary programming assignments.
    """
    t = ' '.join(text.lower().split())
    if not (re.search(r'\btwo\s+arrays\b', t) or
            ('array a' in t and 'array b' in t)):
        return False
    describes_n = bool(re.search(r'\bn\s*[,;:]?\s*(?:the\s+)?number\s+of\s+elements\s+in\s+array\s+a\b', t))
    describes_m = bool(re.search(r'\bm\s*[,;:]?\s*(?:the\s+)?number\s+of\s+elements\s+in\s+array\s+b\b', t))
    n_values = bool(re.search(r'\bn\s+(?:space[- ]separated\s+)?(?:integers|elements|values|numbers)\b', t))
    m_values = bool(re.search(r'\bm\s+(?:space[- ]separated\s+)?(?:integers|elements|values|numbers)\b', t))
    return describes_n and describes_m and n_values and m_values


def _two_array_bounds(text: str):
    """Read only the explicit shared bounds, if provided in the assignment."""
    sizes = re.search(r'(\d+)\s*(?:<=|≤)\s*n\s*,\s*m\s*(?:<=|≤)\s*(\d+)', text, re.I)
    values = re.search(
        r'(-?\d+)\s*(?:<=|≤)\s*A\s*\[\s*i\s*\]\s*,\s*B\s*\[\s*i\s*\]\s*(?:<=|≤)\s*(-?\d+)',
        text, re.I)
    return ((int(sizes.group(1)), int(sizes.group(2))) if sizes else (1, None),
            (int(values.group(1)), int(values.group(2))) if values else (None, None))


def _mentions_counted_array(text: str) -> bool:
    """Detect *explicit* n-counted integer sequences, not arbitrary arrays.

    The rules are intentionally conservative: false rejection of a valid test
    is worse than flagging it for teacher review.
    """
    t = ' '.join(text.lower().split())
    n_declared = bool(re.search(
        r'(?:first (?:line|integer)[^\n.]{0,100}?\b(?:integer\s+)?n\b|'
        r'(?:read|input|given)\s+(?:an?\s+)?(?:integer\s+)?n\s*[,.:]?\s*'
        r'(?:the\s+)?(?:number|size|length))', t))
    counted_elements = bool(re.search(
        r'(?:second (?:line|row)[^.]{0,110}?\b(?:exactly\s+)?n\b[^.]{0,60}?\b(?:integers|elements|numbers|values)\b|'
        r'\b(?:next|following|followed by|then read)\s+(?:exactly\s+)?n\s+'
        r'(?:space[- ]separated\s+)?(?:integers|elements|numbers|values)\b|'
        r'\bn\s+(?:space[- ]separated\s+)?(?:integers|elements|numbers|values)\b)', t))
    # A repeated testcase or matrix may also contain n-sized fields; avoid
    # inferring a simplistic single array in that situation.
    complex_format = bool(re.search(
        r'\b(?:multiple\s+test\s+cases|t\s+test\s+cases|matrix|grid|'
        r'adjacency\s+list|each\s+test\s+case|until\s+eof)\b', t))
    return n_declared and counted_elements and not complex_format


def _size_bounds(text: str) -> tuple[int | None, int | None]:
    match = re.search(r'(-?\d+)\s*(?:<=|≤)\s*n\s*(?:<=|≤)\s*(-?\d+)', text, re.I)
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def _element_bounds(text: str) -> tuple[int | None, int | None]:
    match = re.search(
        r'(?:array\s+element|each\s+element|every\s+array\s+element)[^.\n]{0,140}?'
        r'(?:range|between)\s*\[?\s*(-?\d+)\s*[,–-]\s*(-?\d+)\s*\]?', text, re.I)
    return (int(match.group(1)), int(match.group(2))) if match else (None, None)


def _checker_for_array(text: str):
    """Opt in only for clearly specified single-value maximum/minimum tasks."""
    description = re.split(r'\b(?:input\s+format|sample\s+input|example\s+input)\b',
                           text, maxsplit=1, flags=re.I)[0].lower()
    if re.search(r'\b(?:maximum|largest|greatest)\s+(?:array\s+)?(?:element|value|number)\b', description) \
            and not re.search(r'\b(?:index|position|frequency|second\s+largest)\b', description):
        return max
    if re.search(r'\b(?:minimum|smallest)\s+(?:array\s+)?(?:element|value|number)\b', description) \
            and not re.search(r'\b(?:index|position|frequency|second\s+smallest)\b', description):
        return min
    return None


def review_case(problem_text: str, stdin: str, expected: str | None = None) -> Validation:
    """Check an input using only safe, evidence-backed format rules.

    VALID means the recognized *input structure* is valid, not that the
    assignment is mathematically proven correct.  REVIEW means unsupported
    format.  Known max/min outputs can be recomputed deterministically.
    """
    if not isinstance(stdin, str) or not isinstance(problem_text, str):
        return Validation('invalid', 'Input and assignment text must be strings.')

    # Check two counted arrays before a one-array pattern; otherwise a valid
    # second array size m may be mistaken for an extra element in array A.
    if _mentions_two_counted_arrays(problem_text):
        tokens = stdin.split()
        try:
            numbers = [int(token) for token in tokens]
        except ValueError:
            return Validation('invalid', 'The two-array input format requires integers only.')
        if not numbers:
            return Validation('invalid', 'Missing first array size n.')
        (min_size, max_size), (min_value, max_value) = _two_array_bounds(problem_text)
        n = numbers[0]
        if n < min_size or (max_size is not None and n > max_size):
            return Validation('invalid', f'First array size n={n} is outside the stated bounds.')
        if len(numbers) < n + 2:
            return Validation('invalid', f'First array needs {n} values followed by a second size m.')
        first = numbers[1:n + 1]
        m = numbers[n + 1]
        if m < min_size or (max_size is not None and m > max_size):
            return Validation('invalid', f'Second array size m={m} is outside the stated bounds.')
        second = numbers[n + 2:]
        if len(second) != m:
            return Validation('invalid', f'm={m} but {len(second)} second-array elements were provided.')
        if min_value is not None and any(x < min_value for x in first + second):
            return Validation('invalid', f'An array element is below {min_value}.')
        if max_value is not None and any(x > max_value for x in first + second):
            return Validation('invalid', f'An array element exceeds {max_value}.')
        return Validation('valid', 'Both array sizes and element counts checked; expected output needs teacher verification.')

    # If the problem involves two arrays but the PDF does not describe the
    # counted-array layout clearly enough, do not reject valid inputs by
    # forcing them through the single-array parser.
    if re.search(r'\btwo\s+arrays\b', problem_text, re.I) and \
            re.search(r'\barray\s+a\b', problem_text, re.I) and \
            re.search(r'\barray\s+b\b', problem_text, re.I):
        return Validation('review', 'Two-array input layout needs teacher verification.')

    if _mentions_counted_array(problem_text):
        parts = stdin.split()
        if not parts:
            return Validation('invalid', 'Missing array size n.')
        try:
            numbers = [int(part) for part in parts]
        except ValueError:
            return Validation('invalid', 'The input specification requires integers only.')
        n, elements = numbers[0], numbers[1:]
        lower, upper = _size_bounds(problem_text)
        if n < 1:
            return Validation('invalid', 'Array size must be positive.')
        if lower is not None and n < lower or upper is not None and n > upper:
            return Validation('invalid', f'Array size n={n} is outside the PDF constraints.')
        if len(elements) != n:
            return Validation('invalid', f'n={n} but {len(elements)} array elements were provided.')
        min_element, max_element = _element_bounds(problem_text)
        if min_element is not None and any(x < min_element for x in elements):
            return Validation('invalid', f'An array value is below {min_element}.')
        if max_element is not None and any(x > max_element for x in elements):
            return Validation('invalid', f'An array value exceeds {max_element}.')
        checker = _checker_for_array(problem_text)
        if checker is not None:
            value = f'{checker(elements)}\n'
            return Validation('valid', 'Counted array and expected result checked.', value)
        return Validation('valid', 'Counted array structure checked; output needs verification.')

    # A single text line is also a reusable input shape. Preserve spaces and
    # empty lines: unlike integer inputs, whitespace here is meaningful.
    single_line = bool(re.search(
        r'(?:input\s+consists\s+of|reads?|input\s+is)\s+(?:exactly\s+)?'
        r'(?:one\s+complete\s+line|one\s+line|a\s+single\s+line)\s+of\s+text',
        problem_text, re.I))
    if single_line:
        if '\x00' in stdin or len(stdin.splitlines()) > 1:
            return Validation('invalid', 'Expected a single line of text.')
        description = re.split(r'\b(?:input\s+format|sample\s+input|example\s+input)\b',
                               problem_text, maxsplit=1, flags=re.I)[0].lower()
        if ('count' in description and 'vowel' in description and
                'every occurrence' in problem_text.lower()):
            count = sum(ch in 'aeiouAEIOU' for ch in stdin)
            return Validation('valid', 'One line of text; vowel count checked.', f'{count}\n')
        return Validation('valid', 'One line of text; output needs verification.')

    # A narrow but useful second format: a program that reads exactly two
    # integers.  Do not generalize from examples alone.
    t = ' '.join(problem_text.lower().split())
    if re.search(r'\b(?:read|reads|input|given|accepts?|enter)\s+(?:exactly\s+)?two\s+integers\b', t) \
            and not re.search(r'\b(?:array|matrix|test\s+cases|list\s+of\s+numbers)\b', t):
        pieces = stdin.split()
        if len(pieces) != 2:
            return Validation('invalid', f'Expected two integers, got {len(pieces)} values.')
        try:
            for part in pieces:
                int(part)
        except ValueError:
            return Validation('invalid', 'Both input values must be integers.')
        return Validation('valid', 'Exactly two integers provided; output needs verification.')

    # For arbitrary graphs, strings, matrices, multi-case, interactive, etc.,
    # silently claiming input validity would reproduce the original problem.
    return Validation('review', 'Input format could not be checked automatically; teacher must review it.')
