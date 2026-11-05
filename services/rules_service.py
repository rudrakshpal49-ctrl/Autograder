"""Validate execution limits and configured analyzer parameters.

Old rules are accepted with visible warnings for unknown/inert fields; supported
parameter values are checked before saving or evaluating an assignment.
"""
import json
import math
import re
from services.storage_utils import MAX_RULES_BYTES

DEFAULT_LIMITS = {'problem': 'c_program', 'time_limit_s': 1, 'memory_limit_mb': 64}


def example_rules_bytes():
    return (json.dumps(DEFAULT_LIMITS, indent=2) + '\n').encode('utf-8')


def automatic_rules_bytes():
    return example_rules_bytes()



# These names and their supported parameters are shared by all rule consumers.
SUPPORTED_PARAMS = {
    'compiler_warnings': {'max_warnings', 'penalty_per_warning'},
    'static_analysis': {'max_cyclomatic', 'max_function_length', 'penalty_per_violation'},
    'function_split': {'expect_helpers', 'min_functions', 'penalty_per_missing_function'},
    'llm_review': {'max_line_length', 'penalty_per_issue', 'require_comments', 'use_llm', 'model'},
    'similarity_flag': {'enabled', 'flag_threshold', 'use_ai', 'model', 'min_tokens', 'ai_weight'},
    'io_tests': {'output_comparison'},
    'edge_case_runner': {'output_comparison', 'cases'},
    'sanitizer_run': {'flags', 'max_cases', 'penalty_memory_error',
                      'penalty_undefined_behavior', 'penalty_memory_leak'},
    'complexity_estimator': {
        'required', 'expected_complexity', 'input_sizes', 'adaptive_start_n',
        'adaptive_max_n', 'max_measurements', 'min_successful_measurements',
        'adaptive_refinement_measurements', 'adaptive_recursive_step',
        'ollama_model', 'ollama_timeout_s', 'ollama_complexity_fallback',
        'seed', 'optimization', 'problem_description',
    },
}
INACTIVE_ANALYZERS = set()  # Complexity is available when explicitly configured.
LEGACY_IGNORED_PARAMS = {
    'compiler_warnings': {'flags'},
    'llm_review': {'ai_weight'},
}
COMPARISONS = {'exact', 'trim_trailing'}


def _check_num(name, value, lower, upper, *, integer=False):
    valid = (isinstance(value, int) if integer else isinstance(value, (int, float)))
    if not valid or isinstance(value, bool) or not math.isfinite(value) or not lower <= value <= upper:
        kind = 'integer' if integer else 'number'
        raise ValueError(f'{name} must be a {kind} between {lower} and {upper}.')


def _validate_complexity_params(params: dict) -> None:
    """Bound profiler workloads and reject ineffective instructor parameters.

    The metric must opt in using a teacher-required asymptotic limit. We never
    accept executable expressions, binary paths, arbitrary URLs or shell flags.
    """
    from agent.analyzers.time_growth_bridge import _expected
    from agent.analyzers.growth_instruction_counter import _complexity_rank

    bound = params.get('expected_complexity', params.get('required'))
    if _complexity_rank(_expected(bound)) is None:
        raise ValueError('complexity_estimator.params.required must be a supported Big-O bound.')
    for key, low, high in (
        ('adaptive_start_n', 2, 4096), ('adaptive_max_n', 8, 100000),
        ('max_measurements', 5, 12), ('min_successful_measurements', 5, 12),
        ('adaptive_refinement_measurements', 0, 5),
        ('adaptive_recursive_step', 1, 4096), ('seed', 0, 2147483647),
    ):
        if key in params:
            _check_num(f'complexity_estimator.params.{key}', params[key], low, high, integer=True)
    minimum = params.get('min_successful_measurements', 5)
    maximum = params.get('max_measurements', 7)
    if minimum > maximum:
        raise ValueError('Complexity max_measurements must be >= min_successful_measurements.')
    if 'input_sizes' in params:
        sizes = params['input_sizes']
        if (not isinstance(sizes, list) or not 5 <= len(sizes) <= 12
                or any(isinstance(n, bool) or not isinstance(n, int) or not 2 <= n <= 100000
                       for n in sizes) or len(set(sizes)) != len(sizes)
                or len(sizes) < minimum):
            raise ValueError('complexity input_sizes must be 5-12 unique integer sizes >= 2.')
    if 'ollama_timeout_s' in params:
        _check_num('complexity ollama_timeout_s', params['ollama_timeout_s'], 1, 120)
    if 'ollama_complexity_fallback' in params and not isinstance(params['ollama_complexity_fallback'], bool):
        raise ValueError('complexity ollama_complexity_fallback must be true or false.')
    if 'optimization' in params and params['optimization'] not in ('-O0', '-O1', '-O2'):
        raise ValueError('complexity optimization must be -O0, -O1 or -O2.')
    for key, max_chars in (('ollama_model', 100), ('problem_description', 4000)):
        if key in params and (not isinstance(params[key], str)
                              or not params[key].strip() or len(params[key]) > max_chars):
            raise ValueError(f'complexity {key} must be a nonempty string of at most {max_chars} characters.')


def _validate_params(analyzer: str, params: dict):
    if analyzer == 'complexity_estimator':
        _validate_complexity_params(params)
        return
    if analyzer in INACTIVE_ANALYZERS:
        return
    if analyzer not in SUPPORTED_PARAMS:
        return
    numeric = {
        'max_warnings': (0, 100000, True),
        'penalty_per_warning': (0, 1, False),
        'max_cyclomatic': (1, 100000, True),
        'max_function_length': (1, 100000, True),
        'penalty_per_violation': (0, 1, False),
        'min_functions': (1, 100000, True),
        'penalty_per_missing_function': (0, 1, False),
        'max_line_length': (40, 300, True),
        'penalty_per_issue': (0, 1, False),
        'flag_threshold': (0.50, 1, False),
        'min_tokens': (10, 200, True),
        'ai_weight': (0, 1 if analyzer == 'similarity_flag' else 0.30, False),
        'max_cases': (1, 60, True),
        'penalty_memory_error': (0, 10, False),
        'penalty_undefined_behavior': (0, 10, False),
        'penalty_memory_leak': (0, 10, False),
    }
    for key in params.keys() & numeric.keys():
        a, b, integer = numeric[key]
        _check_num(f'{analyzer}.params.{key}', params[key], a, b, integer=integer)
    for key in ('expect_helpers', 'require_comments', 'use_llm', 'enabled', 'use_ai'):
        if key in params and not isinstance(params[key], bool):
            raise ValueError(f'{analyzer}.params.{key} must be true or false.')
    if 'model' in params:
        if not isinstance(params['model'], str):
            raise ValueError(f'{analyzer}.params.model must be a string.')
        allowed = ({'qwen2.5-coder:3b', 'llama3.2:1b'} if analyzer == 'llm_review'
                   else {'qwen2.5-coder:3b'})
        if params['model'] not in allowed:
            raise ValueError(f'{analyzer}.params.model must be one of {sorted(allowed)}.')
    if 'output_comparison' in params and (not isinstance(params['output_comparison'], str)
                                        or params['output_comparison'] not in COMPARISONS):
        raise ValueError(f'{analyzer}.params.output_comparison must be exact or trim_trailing.')
    if 'cases' in params:
        cases = params['cases']
        if (analyzer != 'edge_case_runner' or not isinstance(cases, list)
                or not 1 <= len(cases) <= 20 or
                any(not isinstance(c, str) or not re.fullmatch(r'[a-z][a-z0-9_]*', c) for c in cases) or
                len(set(cases)) != len(cases)):
            raise ValueError('edge_case_runner.params.cases must be 1-20 unique lowercase scenario IDs.')
    if 'flags' in params:
        if analyzer == 'sanitizer_run':
            from agent.sanitizer_runner import ALLOWED_FLAGS
            if not isinstance(params['flags'], str) or params['flags'] not in ALLOWED_FLAGS:
                raise ValueError('sanitizer_run.params.flags must use approved sanitizer flags.')
        elif not isinstance(params['flags'], (str, list)):
            raise ValueError('compiler_warnings.params.flags must be a string or list (ignored).')


def rules_warnings(rules: dict) -> list[str]:
    """Expose unsupported or inert rubric settings to instructors, never silently drop."""
    warnings = []
    if not isinstance(rules, dict):
        return warnings
    for field in rules.keys() - {'problem', 'time_limit_s', 'memory_limit_mb',
                                  'metrics', 'modularity_options', 'originality_options'}:
        warnings.append(f'Unrecognized top-level rule: {field}.')
    counts = {}
    for i, metric in enumerate(rules.get('metrics', []) or []):
        if not isinstance(metric, dict):
            continue
        analyzer = metric.get('analyzer')
        label = f'metrics[{i}] ({analyzer or "unspecified"})'
        if analyzer in INACTIVE_ANALYZERS:
            warnings.append(f'{label}: reserved analyzer not yet implemented; no grade will be produced.')
        elif analyzer not in SUPPORTED_PARAMS:
            warnings.append(f'{label}: unknown analyzer; no grade will be produced.')
        else:
            counts[analyzer] = counts.get(analyzer, 0) + 1
        for field in metric.keys() - {'name', 'analyzer', 'weight', 'params'}:
            warnings.append(f'{label}: unknown metric field {field}.')
        if 'weight' not in metric:
            warnings.append(f'{label}: no weight specified; an overall grade cannot be calculated for this rubric.')
        for key in (metric.get('params') or {}):
            if key in LEGACY_IGNORED_PARAMS.get(analyzer, set()):
                warnings.append(f'{label}: params.{key} is legacy and ignored.')
            elif key not in SUPPORTED_PARAMS.get(analyzer, set()):
                warnings.append(f'{label}: params.{key} is unsupported and ignored.')
    for analyzer, count in counts.items():
        if count > 1:
            warnings.append(f'{analyzer} is configured {count} times; only the first matching metric is graded.')
    # Reports already combine weighted scores when all enabled metrics complete.
    # Warn only when the configured weights cannot produce a total grade.
    metrics = [m for m in (rules.get('metrics') or []) if isinstance(m, dict)]
    if metrics and all('weight' in m and isinstance(m['weight'], (int, float))
                       and not isinstance(m['weight'], bool) for m in metrics):
        total = sum(m['weight'] for m in metrics)
        if abs(total - 1.0) > 1e-6:
            warnings.append(
                f'Metric weights total {total * 100:g}%, not 100%; '
                'the final weighted grade will be unavailable.')
    for options in ('modularity_options', 'originality_options'):
        supported = (SUPPORTED_PARAMS['function_split'] if options == 'modularity_options'
                     else SUPPORTED_PARAMS['similarity_flag'])
        for key in rules.get(options, {}):
            if key not in supported:
                warnings.append(f'{options}.{key} is unsupported and ignored.')
        if options in rules and rules.get('metrics'):
            warnings.append(f'{options} only provides a preview when the matching metric is not configured.')
    return warnings


def get_edge_types(rules: dict) -> list[str]:
    """Read teacher-requested logical scenarios from the configured metric."""
    for metric in rules.get('metrics', []) or []:
        if isinstance(metric, dict) and metric.get('analyzer') == 'edge_case_runner':
            return list((metric.get('params') or {}).get('cases', []))
    return []


def validate_rules(content: bytes):
    if not isinstance(content, bytes) or not content or len(content) > MAX_RULES_BYTES:
        raise ValueError('Execution rules must be a nonempty JSON file up to 128 KiB.')
    try:
        rules = json.loads(content.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError('Execution rules must contain valid UTF-8 JSON.') from exc
    if not isinstance(rules, dict):
        raise ValueError('Execution rules must be a JSON object.')
    t = rules.get('time_limit_s')
    m = rules.get('memory_limit_mb')
    if isinstance(t, bool) or not isinstance(t, (float, int)) or not math.isfinite(t) or not 0 < t <= 30:
        raise ValueError('time_limit_s must be a positive number no greater than 30.')
    if isinstance(m, bool) or not isinstance(m, int) or not 16 <= m <= 4096:
        raise ValueError('memory_limit_mb must be an integer between 16 and 4096.')
    if 'metrics' in rules:
        metrics = rules['metrics']
        if not isinstance(metrics, list) or len(metrics) > 30:
            raise ValueError('metrics must be a list of at most 30 analyzer entries.')
        for i, metric in enumerate(metrics):
            if not isinstance(metric, dict):
                raise ValueError(f'metrics[{i}] must be a JSON object.')
            if 'weight' in metric:
                _check_num(f'metrics[{i}].weight', metric['weight'], 0, 1)
            params = metric.get('params', {})
            if not isinstance(params, dict):
                raise ValueError(f'metrics[{i}].params must be a JSON object.')
            _validate_params(metric.get('analyzer', ''), params)
    for opts in ('modularity_options', 'originality_options'):
        if opts in rules:
            if not isinstance(rules[opts], dict):
                raise ValueError(f'{opts} must be a JSON object.')
            _validate_params('function_split' if opts == 'modularity_options' else 'similarity_flag', rules[opts])
    return rules


def get_modularity_preferences(rules: dict) -> tuple[bool, int]:
    """Read the teacher's helper requirement from the assignment rules.

    A configured function_split metric takes priority. For execution-only
    rules, an optional modularity_options block holds the same choice.
    """
    for metric in rules.get('metrics', []):
        if isinstance(metric, dict) and metric.get('analyzer') == 'function_split':
            params = metric.get('params', {})
            required = params.get('expect_helpers', False)
            return bool(required), params.get('min_functions', 2 if required else 1)
    options = rules.get('modularity_options', {})
    return bool(options.get('expect_helpers', False)), options.get('min_functions', 2)


def set_modularity_preferences(content: bytes, require_helpers: bool,
                               min_functions: int = 2) -> bytes:
    """Apply the teacher's choice without changing other rubric metrics/weights."""
    if not isinstance(require_helpers, bool):
        raise ValueError('Require helper functions must be yes or no.')
    if (isinstance(min_functions, bool) or not isinstance(min_functions, int)
            or not 2 <= min_functions <= 50):
        raise ValueError('Minimum functions must be an integer between 2 and 50.')

    rules = validate_rules(content)
    required_count = min_functions if require_helpers else 1
    metric_found = False
    for metric in rules.get('metrics', []):
        if isinstance(metric, dict) and metric.get('analyzer') == 'function_split':
            params = metric.setdefault('params', {})
            if not isinstance(params, dict):
                raise ValueError('Modularity params must be a JSON object.')
            params['expect_helpers'] = require_helpers
            params['min_functions'] = required_count
            metric_found = True

    if metric_found:
        rules.pop('modularity_options', None)
    else:
        # With execution-only rules this configures the existing preview.
        # Do not silently insert a weighted metric into a custom rubric.
        rules['modularity_options'] = {
            'expect_helpers': require_helpers,
            'min_functions': required_count,
        }

    result = (json.dumps(rules, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    validate_rules(result)
    if len(result) > MAX_RULES_BYTES:
        raise ValueError('Updated grading rules exceed 128 KiB.')
    return result


def get_originality_preferences(rules: dict) -> tuple[bool, float, bool]:
    """Return enabled, token similarity flag threshold, and optional 20% AI peer review."""
    for metric in rules.get('metrics', []):
        if isinstance(metric, dict) and metric.get('analyzer') == 'similarity_flag':
            params = metric.get('params', {})
            return params.get('enabled', True), params.get('flag_threshold', 0.85), params.get('use_ai', True)
    options = rules.get('originality_options', {})
    return (bool(options.get('enabled', False)),
            options.get('flag_threshold', 0.85), bool(options.get('use_ai', True)))


def set_originality_preferences(content: bytes, enabled: bool, flag_threshold: float = 0.85,
                                use_ai: bool = True) -> bytes:
    """Apply teacher choices without silently adding a weighted rubric metric."""
    if not isinstance(enabled, bool) or not isinstance(use_ai, bool):
        raise ValueError('Originality options must be true or false.')
    if (isinstance(flag_threshold, bool) or not isinstance(flag_threshold, (int, float))
            or not math.isfinite(flag_threshold) or not 0.50 <= flag_threshold <= 1):
        raise ValueError('Originality similarity threshold must be between 0.50 and 1.0.')
    rules = validate_rules(content)
    metrics = [m for m in rules.get('metrics', []) if isinstance(m, dict)
               and m.get('analyzer') == 'similarity_flag']
    if metrics:
        # Cannot disable a metric while retaining its instructor-defined weight.
        # Explicit disabling is stored in params rather than removing the metric.
        for metric in metrics:
            params = metric.setdefault('params', {})
            if not isinstance(params, dict):
                raise ValueError('Originality params must be a JSON object.')
            params.update({'enabled': enabled, 'flag_threshold': float(flag_threshold),
                           'use_ai': use_ai})  # Preserve custom ai_weight in uploaded rules.
        rules.pop('originality_options', None)
    else:
        rules['originality_options'] = {
            'enabled': enabled, 'flag_threshold': float(flag_threshold),
            'use_ai': use_ai, 'ai_weight': 0.20,
        }
    new_content = (json.dumps(rules, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
    validate_rules(new_content)
    if len(new_content) > MAX_RULES_BYTES:
        raise ValueError('Updated grading rules exceed 128 KiB.')
    return new_content
