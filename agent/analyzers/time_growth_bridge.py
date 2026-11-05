"""Opt-in time-complexity grading for AutoGrader using uploaded Callgrind profiler.

This adapter intentionally does not replace other analyzers. The underlying
modules retain their source-analysis and fitter behavior but use new names.
An empirical, confidently fitted result is required before marks are awarded.
Neither an LLM suggestion nor a failed profiler is treated as a grade.
"""
from __future__ import annotations

import math
import os
import re
import shutil
import tempfile

from agent.analyzers import growth_instruction_counter as profiler


def _not_scored(status: str, reason: str, **evidence) -> dict:
    return {'status': status, 'score': None,
            'evidence': evidence, 'feedback': reason}


def _expected(value: object) -> str:
    text = str(value or '').strip()
    canonical = re.sub(r'\s+', '', text).lower()
    aliases = {
        'o(nlogn)': 'O(N log N)',
        'o(nlog^2n)': 'O(N (log N)^2)',
        'o(logn)': 'O(log N)',
        'o(sqrtn)': 'O(N^0.5)',
        'o(n)': 'O(N)', 'o(1)': 'O(1)',
        'o(n^2)': 'O(N^2)', 'o(n^3)': 'O(N^3)',
    }
    return aliases.get(canonical, text)


def analyze_time_growth(context: dict, params: dict) -> dict:
    """Return the project's standard ``status/score/evidence/feedback`` record.

    Analysis is enabled only for instructor-configured complexity metrics and
    only after the coordinator has verified a successful smoke run.
    """
    params = params or {}
    expected = _expected(params.get('expected_complexity', params.get('required')))
    if profiler._complexity_rank(expected) is None:
        return _not_scored('insufficient_evidence',
                           'Specify a supported required time bound in rules.json, '
                           'for example O(N) or O(N^2).',
                           expected_complexity=expected)

    source = context.get('source')
    if not isinstance(source, (bytes, bytearray)) or not source.strip():
        return _not_scored('unavailable', 'C source is missing or empty.',
                           expected_complexity=expected)

    # Windows's native GCC cannot use the Valgrind instruction counter.
    # Never substitute wall-clock timing for Callgrind and award marks.
    has_valgrind = shutil.which('valgrind') is not None
    has_headers = profiler._callgrind_header() is not None
    if os.name == 'nt' or not has_valgrind or not has_headers:
        return _not_scored(
            'unavailable',
            'Callgrind is required for this analyzer. Run the AutoGrader '
            'in Linux/WSL2 with valgrind and its development headers. '
            'No complexity score was assigned.',
            expected_complexity=expected,
            detected_complexity='inconclusive',
            valgrind_available=has_valgrind,
            callgrind_header_available=has_headers,
        )

    configuration = dict(params)
    configuration['expected_complexity'] = expected
    configuration['sample_input'] = context.get('sample_input') or ''
    configuration.setdefault('ollama_model', 'qwen2.5-coder:3b')
    configuration.setdefault('ollama_complexity_fallback', False)
    configuration.setdefault('min_successful_measurements', 5)
    configuration.setdefault('max_measurements', 7)
    configuration.setdefault('adaptive_max_n', 128)
    configuration.setdefault('adaptive_refinement_measurements', 2)
    configuration.setdefault('seed', 17)
    configuration['adaptive_input_sizes'] = not bool(configuration.get('input_sizes'))

    # Use rules' input grammar/size configuration, if present. Otherwise the
    # uploaded size_aware_stdin analyzer attempts Ollama + static C fallback.
    # Problem statement text may be supplied later; never invent its meaning.
    limit = min(10.0, max(1.0, float(context.get('time_limit_s', 1.0)) * 5))
    try:
        with tempfile.TemporaryDirectory(prefix='autograder_growth_') as directory:
            source_path = os.path.join(directory, 'submission.c')
            with open(source_path, 'wb') as stream:
                stream.write(source)
            # Prevent cached input-specs leaking across unrelated submissions.
            configuration['input_spec_dir'] = os.path.join(directory, 'input_specs')
            evaluated = profiler.evaluate(
                source_path, params=configuration,
                limits={'time_limit_s': limit},
            )
    except Exception as exc:
        return _not_scored('insufficient_evidence',
                           'Complexity profiling could not finish; no marks assigned.',
                           expected_complexity=expected,
                           profiler_error=f'{type(exc).__name__}: {exc}')

    evidence = dict(evaluated.get('evidence') or {})
    evidence['expected_complexity'] = expected
    empirical = evidence.get('empirical_detected_complexity', 'inconclusive')
    fit = evidence.get('fit') or {}
    trials = evidence.get('trials') or []
    confidence = fit.get('confidence')
    source_kind = evidence.get('complexity_estimate_source')
    reliable = (len(trials) >= 5 and empirical != 'inconclusive'
                and confidence in ('high', 'medium')
                and source_kind == 'Callgrind model fitting'
                and all(row.get('method') == 'callgrind_instructions' for row in trials))
    if not reliable:
        # Preserves any advisory LLM estimate as evidence for the teacher only.
        return _not_scored(
            'insufficient_evidence',
            'Insufficient reliable empirical evidence to grade time complexity. '
            'Check benchmark input grammar, sample sizes, and profiler diagnostics.',
            **evidence,
        )

    # The source profiler already implements ordering for common polynomial,
    # logarithmic, exponential and factorial bounds; do not reimplement it.
    measured_rank = profiler._complexity_rank(empirical)
    required_rank = profiler._complexity_rank(expected)
    if measured_rank is None or required_rank is None:
        return _not_scored('insufficient_evidence',
                           'Unable to compare the measured complexity and teacher requirement.',
                           **evidence)
    grade = 1.0 if measured_rank <= required_rank else 0.4
    if not math.isfinite(grade):
        return _not_scored('insufficient_evidence', 'Invalid complexity result.', **evidence)
    evidence['detected_complexity'] = empirical
    evidence['score_basis'] = 'empirical_callgrind_only'
    return {
        'status': 'completed', 'score': grade, 'evidence': evidence,
        'feedback': (f'Estimated {empirical}; required {expected}. '
                     f'Callgrind confidence: {confidence}. '
                     'Finite-input measurements do not prove asymptotic Big-O.'),
    }
