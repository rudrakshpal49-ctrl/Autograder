"""Run approved correctness tests and privately persist per-submission reports."""
import json
import math
from sqlalchemy import select
from database.db import SessionLocal
from database.orm_models import Assignment, Submission, CorrectnessResult, FirstPassResult
from services.authentication import require_role
from services.rules_service import validate_rules
from services.storage_utils import read_private
from agent.sandbox import run_c_tests
from analyzers.correctness import evaluate_correctness


def _lookup(session, user_id, submission_id):
    user = require_role(user_id, {'teacher', 'student'})
    submission = session.get(Submission, submission_id)
    if not submission:
        raise ValueError('Submission not found.')
    assignment = session.get(Assignment, submission.assignment_id)
    if not assignment:
        raise ValueError('Assignment not found.')
    if user['role'] == 'teacher' and assignment.created_by != user_id:
        raise PermissionError('You cannot access another teacher\'s evaluation.')
    if user['role'] == 'student' and submission.student_id != user_id:
        raise PermissionError('You cannot access another student\'s evaluation.')
    return user['role'], submission, assignment


def _fresh(report, submission, assignment):
    return (report.get('source_hash') == submission.source_hash and
            report.get('testset_hash') == assignment.testset_hash and
            report.get('reference_hash') == assignment.reference_hash and
            report.get('rules_hash') == assignment.rules_hash and
            bool(assignment.tests_approved))


def get_correctness(teacher_id: int, submission_id: int):
    """Never serve detailed correctness reports to students."""
    require_role(teacher_id, {'teacher'})
    with SessionLocal() as session:
        _, sub, assignment = _lookup(session, teacher_id, submission_id)
        row = session.scalar(select(CorrectnessResult).where(CorrectnessResult.submission_id == submission_id))
        if not row:
            return None
        result = json.loads(row.report_json)
        result['stale'] = not _fresh(result, sub, assignment)
        return result


def run_correctness(user_id: int, submission_id: int):
    """Student may trigger own evaluation, but receives no grades or evidence."""
    with SessionLocal() as session:
        role, sub, a = _lookup(session, user_id, submission_id)
        if not a.tests_approved or not a.testset_json:
            # No grading until tests are teacher-approved.
            return {'submission_id': submission_id, 'status': 'not_configured'} if role == 'teacher' else {'submission_id': submission_id}
        if not a.rules_path:
            raise ValueError('Grading rules are missing.')
        rules = validate_rules(read_private(a.rules_path, 'rules'))
        metric = next((m for m in rules['metrics'] if m['analyzer'] == 'io_tests'), None)
        if not metric:
            raise ValueError('The rubric does not enable correctness testing.')
        source = read_private(sub.source_path, 'submissions')
        testset = json.loads(a.testset_json)
        # Output comparison belongs to the teacher's JSON grading rubric, not
        # an LLM-guessed input contract.  It stays fixed for every submission.
        policy = (metric.get('params') or {}).get('output_comparison', {'mode': 'trim_trailing'})
        if not isinstance(policy, dict) or policy.get('mode', 'trim_trailing') not in {
                'exact', 'trim_trailing', 'tokens', 'unordered_tokens',
                'unordered_lines', 'float_tokens'}:
            raise ValueError('Invalid correctness output_comparison in grading rules.')
        if policy.get('mode') == 'float_tokens':
            for name in ('abs_tol', 'rel_tol'):
                value = policy.get(name, 1e-6)
                if isinstance(value, bool) or not isinstance(value, (int, float)) or \
                        not math.isfinite(value) or value < 0:
                    raise ValueError(f'{name} must be a finite nonnegative tolerance.')
        prior = session.scalar(select(FirstPassResult).where(FirstPassResult.submission_id == submission_id))
        # Only honor a first pass for the same source and rubric.
        initial = json.loads(prior.report_json) if prior else None
        if initial and initial.get('source_hash') == sub.source_hash and initial.get('rules_hash') == a.rules_hash and (
            initial.get('compiler', {}).get('status') in {'failed', 'timeout'} or
            initial.get('smoke', {}).get('status') in {'timeout', 'crashed', 'resource_limit', 'output_limit'}
        ):
            result = {'status': 'skipped', 'score': None, 'passed': 0, 'total': len(testset), 'results': [],
                      'message': 'First pass blocked execution; review its diagnostics.'}
        else:
            execution = run_c_tests(source, [case['input'] for case in testset],
                                     time_limit_s=rules['time_limit_s'],
                                     memory_limit_mb=rules['memory_limit_mb'])
            result = evaluate_correctness(testset, execution, comparison=policy)
        result.update({'submission_id': submission_id, 'source_hash': sub.source_hash,
                       'rules_hash': a.rules_hash, 'testset_hash': a.testset_hash,
                       'reference_hash': a.reference_hash,
                       'weight': metric['weight'],
                       'image_id': execution.get('image_id') if 'execution' in locals() else None})
        row = session.scalar(select(CorrectnessResult).where(CorrectnessResult.submission_id == submission_id))
        if row is None:
            row = CorrectnessResult(submission_id=submission_id)
            session.add(row)
        row.report_json = json.dumps(result, ensure_ascii=False)
        session.commit()
        return result if role == 'teacher' else {'submission_id': submission_id}
