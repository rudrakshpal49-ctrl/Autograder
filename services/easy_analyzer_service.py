"""Teacher-only Syntax, Structure, and Modularity reports.

These are partial rubric scores. Other pending metrics are intentionally NOT treated
as zeros and no final grade is produced here.
"""
import json
from sqlalchemy import select

from agent.first_pass import evaluate_source
from analyzers.syntax import evaluate_syntax
from analyzers.source_metrics import analyze_functions
from analyzers.structure import evaluate_structure
from analyzers.modularity import evaluate_modularity
from database.db import SessionLocal
from database.orm_models import Assignment, Submission, FirstPassResult, EasyAnalyzerResult
from services.authentication import require_role
from services.rules_service import validate_rules
from services.storage_utils import read_private

EASY_ANALYZERS = {'compiler_warnings', 'static_analysis', 'function_split'}


def _load_for_teacher(session, teacher_id, submission_id):
    require_role(teacher_id, {'teacher'})
    sub = session.get(Submission, submission_id)
    if not sub:
        raise ValueError('Submission not found.')
    assignment = session.get(Assignment, sub.assignment_id)
    if not assignment or assignment.created_by != teacher_id:
        raise PermissionError('Only the assignment teacher may view grading reports.')
    return sub, assignment


def get_easy_analyzers(teacher_id: int, submission_id: int):
    with SessionLocal() as session:
        sub, a = _load_for_teacher(session, teacher_id, submission_id)
        row = session.scalar(select(EasyAnalyzerResult).where(EasyAnalyzerResult.submission_id == submission_id))
        if not row:
            return None
        data = json.loads(row.report_json)
        data['stale'] = (data.get('source_hash') != sub.source_hash or
                         data.get('rules_hash') != a.rules_hash)
        return data


def run_easy_analyzers(teacher_id: int, submission_id: int):
    """Runs only the three configured metrics, reusing fresh first-pass evidence."""
    with SessionLocal() as session:
        sub, a = _load_for_teacher(session, teacher_id, submission_id)
        if not a.rules_path:
            raise ValueError('Upload grading rules for this assignment first.')
        rules = validate_rules(read_private(a.rules_path, 'rules'))
        configured = [m for m in rules['metrics'] if m['analyzer'] in EASY_ANALYZERS]
        if not configured:
            raise ValueError('Rules do not enable Syntax, Structure, or Modularity.')
        source_bytes = read_private(sub.source_path, 'submissions')
        initial = session.scalar(select(FirstPassResult).where(FirstPassResult.submission_id == sub.id))
        first = json.loads(initial.report_json) if initial else None
        if not first or first.get('source_hash') != sub.source_hash or first.get('rules_hash') != a.rules_hash:
            # First Pass executes the compile/smoke checks only inside Docker.
            # A missing Docker environment does NOT prevent the source-only checks.
            sample = a.sample_input or None
            if sample is None and a.tests_approved and a.testset_json:
                approved = json.loads(a.testset_json)
                if approved:
                    sample = approved[0]['input']
            first = evaluate_source(source_bytes, sample_input=sample,
                                    time_limit_s=rules['time_limit_s'],
                                    memory_limit_mb=rules['memory_limit_mb'],
                                    rules_hash=a.rules_hash)
            if initial is None:
                initial = FirstPassResult(submission_id=sub.id)
                session.add(initial)
            initial.status = first['status']
            initial.report_json = json.dumps(first, ensure_ascii=False)
        # Analyze the original source code, even if compilation or smoke run fails.
        source_metrics = analyze_functions(source_bytes.decode('utf-8'))
        compiled = first.get('compiler') or {}
        items = []
        for m in configured:
            analyzer = m['analyzer']
            params = m.get('params') or {}
            if analyzer == 'compiler_warnings':
                result = evaluate_syntax(compiled, params)
            elif analyzer == 'static_analysis':
                result = evaluate_structure(source_metrics, params)
            else:
                result = evaluate_modularity(source_metrics, params)
            score = result['score']
            items.append({
                'name': m['name'], 'analyzer': analyzer,
                'weight': m['weight'], **result,
                'contribution': round(100 * m['weight'] * score, 2) if score is not None else None,
            })
        report = {
            'submission_id': sub.id,
            'source_hash': sub.source_hash,
            'rules_hash': a.rules_hash,
            'status': 'complete' if all(m['score'] is not None or m['status'] == 'skipped' for m in items) else 'partial',
            'metrics': items,
            'available_points': round(sum(100 * m['weight'] for m in items if m['score'] is not None), 2),
            'earned_points': round(sum(m['contribution'] for m in items if m['contribution'] is not None), 2),
            'message': 'Partial metric scores only. Remaining analyzers are not yet graded.',
        }
        row = session.scalar(select(EasyAnalyzerResult).where(EasyAnalyzerResult.submission_id == sub.id))
        if row is None:
            row = EasyAnalyzerResult(submission_id=sub.id)
            session.add(row)
        row.report_json = json.dumps(report, ensure_ascii=False)
        session.commit()
        return report
