"""Deterministic rubric-weighted grading from persisted analyzer results.

Missing/unavailable scores do not block the grade: their weight is excluded and
remaining scores are normalized to 100 (provisional). A first-pass-blocked
execution analyzer is different: it gets a genuine 0 that counts against its
rubric weight. Invalid/missing rubrics still cannot produce a grade.
"""
from __future__ import annotations

import math

METRIC_FIELDS = {
    'io_tests': 'correctness_grading',
    'edge_case_runner': 'logical_grading',
    'compiler_warnings': 'syntax_grading',
    'static_analysis': 'structure_grading',
    'function_split': 'modularity_grading',
    'sanitizer_run': 'memory_safety_grading',
    'complexity_estimator': 'complexity_grading',
    'llm_review': 'readability_grading',
    'similarity_flag': 'originality_grading',
}


def _valid_fraction(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and 0 <= value <= 1)


def _gradeable(result: dict) -> bool:
    if not _valid_fraction(result.get('score')):
        return False
    if result.get('status') == 'completed':
        return True
    # Only a gated skip is a scored zero. Other skipped/unavailable analyzers
    # must be treated as N/A, never silently penalized.
    evidence = result.get('evidence') or {}
    return (result.get('status') == 'skipped' and result.get('score') == 0
            and isinstance(evidence, dict)
            and evidence.get('first_pass_blocked') is True)


def calculate_final_grade(report: dict, rules: dict) -> dict:
    """Return a complete/provisional grade and the weight of excluded metrics."""
    metrics = rules.get('metrics', [])
    breakdown, missing, seen = [], [], set()
    if not isinstance(metrics, list) or not metrics:
        return {'status': 'not_configured', 'score': None, 'breakdown': [],
                'missing': ['No weighted rubric configured.'], 'provisional': False}

    weights, invalid = [], []
    for metric in metrics:
        if not isinstance(metric, dict):
            invalid.append('Invalid rubric metric.')
            continue
        analyzer = metric.get('analyzer')
        label = str(metric.get('name') or analyzer or 'Unknown')
        weight = metric.get('weight')
        weights.append(weight)
        field = METRIC_FIELDS.get(analyzer)
        if analyzer in seen or field is None:
            invalid.append(f'{label}: unknown or duplicate analyzer.')
            continue
        seen.add(analyzer)
        stored = report.get(field)
        stored = stored if isinstance(stored, dict) else {}
        valid = _gradeable(stored) and _valid_fraction(weight)
        score = stored.get('score') if valid else None
        contribution = round(100 * weight * score, 6) if valid else None
        breakdown.append({
            'name': label, 'analyzer': analyzer,
            'status': stored.get('status', 'pending'),
            'weight': weight if _valid_fraction(weight) else None,
            'score': score, 'contribution': contribution,
        })
        if not valid:
            missing.append(f'{label}: {stored.get("status", "pending")}')

    if len(weights) != len(metrics) or not all(_valid_fraction(w) for w in weights):
        invalid.append('Every rubric metric needs a weight from 0 to 1.')
    elif not math.isclose(sum(weights), 1.0, rel_tol=0, abs_tol=1e-6):
        invalid.append('Rubric weights must total 100%.')
    if invalid:
        return {'status': 'incomplete', 'score': None, 'breakdown': breakdown,
                'missing': invalid + missing, 'provisional': False}

    scored = [entry for entry in breakdown if entry['contribution'] is not None]
    available_weight = sum(entry['weight'] for entry in scored)
    if available_weight <= 0:
        return {'status': 'incomplete', 'score': None, 'breakdown': breakdown,
                'missing': missing or ['No analyzer supplied a valid grade.'],
                'provisional': False, 'graded_weight': 0.0}

    total = sum(entry['contribution'] for entry in scored)
    provisional = bool(missing)
    return {
        'status': 'provisional' if provisional else 'complete',
        'score': round(total / available_weight, 2),
        'breakdown': breakdown, 'missing': missing, 'provisional': provisional,
        'graded_weight': round(available_weight, 6),
        'excluded_weight': round(1 - available_weight, 6),
    }
