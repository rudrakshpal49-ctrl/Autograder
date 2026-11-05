"""Source-only modularity grading for C programs using Lizard function data.

Counts *definitions*, not declarations or calls. It does not execute student
code. The instructor decides whether helper functions are required.
"""

import math

from agent.analyzers.static_analysis import _functions_from_lizard


def analyze_modularity(context: dict, params: dict) -> dict:
    """Return a normalized modularity score and the functions found.

    Rule parameters:
      expect_helpers: bool (default False; a tiny main-only task can be valid)
      min_functions: positive int (default 2 if helpers expected, else 1)
      penalty_per_missing_function: float 0..1 (default 0.3)

    One missing helper counts as a missing function, not a second penalty.
    """
    expect_helpers = params.get('expect_helpers', False)
    min_functions = params.get('min_functions', 2 if expect_helpers else 1)
    penalty = params.get('penalty_per_missing_function', 0.3)

    if not isinstance(expect_helpers, bool):
        raise ValueError('expect_helpers must be true or false.')
    if isinstance(min_functions, bool) or not isinstance(min_functions, int) or min_functions < 1:
        raise ValueError('min_functions must be a positive integer.')
    if (isinstance(penalty, bool) or not isinstance(penalty, (float, int))
            or not math.isfinite(penalty) or not 0 <= penalty <= 1):
        raise ValueError('penalty_per_missing_function must be between 0 and 1.')

    source = context['source']
    if isinstance(source, bytes):
        source = source.decode('utf-8')
    if not isinstance(source, str):
        raise ValueError('Modularity analyzer requires C source text.')

    try:
        functions = _functions_from_lizard(source)
    except RuntimeError as exc:
        return {
            'status': 'unavailable', 'score': None,
            'evidence': {'functions': [], 'function_count': 0},
            'feedback': str(exc),
        }

    names = [func['name'] for func in functions]
    # C submissions have a main() entrypoint; every other defined function
    # counts as a helper. Lizard does not count ordinary function calls.
    helpers = [name for name in names if name != 'main']
    count = len(names)
    missing = max(0, min_functions - count)
    if expect_helpers and not helpers:
        missing = max(1, missing)

    if count == 0:
        score = 0.0
        feedback = 'No function definitions found; modularity score is 0.'
    else:
        score = round(max(0.0, 1.0 - missing * penalty), 4)
        if missing:
            feedback = (f'{count} function(s) defined; {missing} more required by the rules. '
                        + ('Add a helper function instead of keeping everything in main.'
                           if expect_helpers and not helpers else
                           'Split the task into additional meaningful functions.'))
        else:
            feedback = f'{count} function(s) defined; modularity requirements met.'

    return {
        'status': 'completed',
        'score': score,
        'evidence': {
            'functions': functions[:100],
            'function_names': names[:100],
            'function_count': count,
            'helper_count': len(helpers),
            'helper_functions': helpers[:100],
            'min_functions': min_functions,
            'expect_helpers': expect_helpers,
            'missing_functions': missing,
        },
        'feedback': feedback,
    }
