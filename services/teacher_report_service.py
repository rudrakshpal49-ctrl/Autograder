"""Teacher-only JSON export of a student's persisted AutoGrader evaluation.

No evaluation is rerun. The export is assembled on download so that the
saved grading data remains the single source of truth. This endpoint must
never be used by student views: the detailed evidence can contain private
approved tests and comparison details about other submissions.
"""
from __future__ import annotations

import json
import math

from sqlalchemy import select

from database.db import SessionLocal
from database.orm_models import Assignment, FirstPassResult, Submission, User
from services.authentication import require_role
from services.dates import as_local
from services.report_service import METRICS, _build_evaluation, USEFULNESS, LIMITATIONS
from services.storage_utils import read_private


def build_teacher_report(meta: dict, stored: dict, rules: dict) -> dict:
    """Convert saved results into the JSON structure shown in the assignment.

    An unavailable analyzer stays null and its weight is excluded from the
    provisional total. First-pass-blocked execution analyzers have score zero.
    """
    summary = _build_evaluation(meta, stored, rules)
    rows = []
    lookup = {analyzer: (field, label) for analyzer, field, label in METRICS}
    for safe_metric in summary['metrics']:
        field, label = lookup[safe_metric['analyzer']]
        raw = stored.get(field)
        raw = raw if isinstance(raw, dict) else {}
        rows.append({
            'name': field.removesuffix('_grading'),
            'analyzer': safe_metric['analyzer'],
            'label': label,
            'status': safe_metric['status'],
            'score': safe_metric['score'],
            'weight': safe_metric['weight'],
            'contribution': safe_metric['contribution'],
            'feedback': raw.get('feedback', ''),
            'evidence': raw.get('evidence') or {},
        })

    compiler = stored.get('compiler') or {}
    smoke = stored.get('smoke') or {}
    flags = stored.get('flags') or []
    output = {
        'submission': meta['filename'],
        'submission_id': meta.get('submission_id'),
        'student': meta['student'],
        'assignment': meta['assignment'],
        'evaluated_at': as_local(meta['evaluated_at']).isoformat(),
        'rules_hash': stored.get('rules_hash') or meta.get('rules_hash'),
        'grade': summary['overall_score'],
        'grade_status': ('provisional' if summary['provisional'] else 'complete')
                        if summary['overall_score'] is not None else 'unavailable_incomplete_rubric',
        'first_pass': {
            'status': stored.get('status', 'unknown'),
            'compiled': compiler.get('status') == 'passed',
            'smoke_ok': smoke.get('status') == 'passed',
            'suspected_infinite_loop': 'suspected_infinite_loop' in flags,
            'compiler': compiler,
            'smoke': smoke,
            'static_sanity': stored.get('static_sanity') or {},
        },
        'metrics': rows,
        'flags': flags,
        'suggestions': summary['suggestions'],
        'usefulness_limitations': {
            'usefulness': list(USEFULNESS),
            'limitations': list(LIMITATIONS),
        },
    }
    if stored.get('tool_versions'):
        output['tool_versions'] = stored['tool_versions']
    return output


def _json_safe(value):
    """Keep the exported file valid JSON even if old evidence has NaN/Infinity."""
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if isinstance(value, list):
        return [_json_safe(item) for item in value]
    if isinstance(value, dict):
        return {key: _json_safe(item) for key, item in value.items()}
    return value


def get_teacher_report_json(teacher_id: int, submission_id: int) -> bytes | None:
    """Return UTF-8 JSON for the assigned teacher, or None before evaluation.

    Authorization occurs in the service, not merely the Streamlit interface.
    A student or another instructor can never access the full evidence.
    """
    require_role(teacher_id, {'teacher'})
    with SessionLocal() as session:
        submission = session.get(Submission, submission_id)
        if submission is None:
            raise ValueError('Submission not found.')
        assignment = session.get(Assignment, submission.assignment_id)
        if assignment is None:
            raise ValueError('Assignment not found.')
        if assignment.created_by != teacher_id:
            raise PermissionError('You cannot download another teacher\'s report.')
        evaluation = session.scalar(select(FirstPassResult).where(
            FirstPassResult.submission_id == submission_id))
        if evaluation is None:
            return None
        student = session.get(User, submission.student_id)
        if student is None:
            raise ValueError('Student not found.')

        rules = {}
        if assignment.rules_path:
            try:
                rules = json.loads(read_private(assignment.rules_path, 'rules').decode('utf-8'))
            except (OSError, UnicodeError, ValueError):
                # Grading remains unchanged; incomplete rubric means grade:null.
                rules = {}
        meta = {
            'filename': submission.original_filename or 'source.c',
            'student': student.full_name or student.username,
            'assignment': assignment.title,
            'evaluated_at': evaluation.evaluated_at,
            'rules_hash': assignment.rules_hash,
            'submission_id': submission.id,
        }
        report = build_teacher_report(meta, json.loads(evaluation.report_json), rules)
        return (json.dumps(_json_safe(report), indent=2, ensure_ascii=False, allow_nan=False) + '\n').encode('utf-8')
