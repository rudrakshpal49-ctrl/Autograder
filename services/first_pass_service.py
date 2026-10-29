"""Access-controlled first-pass grading orchestration and DB persistence."""
import json
from sqlalchemy import select
from database.db import SessionLocal
from database.orm_models import Assignment, FirstPassResult, Submission
from services.authentication import require_role
from services.rules_service import validate_rules
from services.storage_utils import read_private
from agent.first_pass import evaluate_source


def _allowed_to_run(user_id: int, sub: Submission, assignment: Assignment) -> str:
    """Students may trigger checks on their own uploads, but cannot read reports."""
    user = require_role(user_id, {'student', 'teacher'})
    if user['role'] == 'student' and sub.student_id != user_id:
        raise PermissionError('You cannot evaluate another student\'s submission.')
    if user['role'] == 'teacher' and assignment.created_by != user_id:
        raise PermissionError('You cannot evaluate another teacher\'s submission.')
    return user['role']


def get_first_pass(user_id: int, submission_id: int):
    """Only the assignment's teacher can retrieve evaluation details."""
    require_role(user_id, {'teacher'})
    with SessionLocal() as session:
        sub = session.get(Submission, submission_id)
        if sub is None:
            raise ValueError('Submission not found.')
        assignment = session.get(Assignment, sub.assignment_id)
        if assignment is None:
            raise ValueError('Assignment not found.')
        if assignment.created_by != user_id:
            raise PermissionError('You cannot inspect another teacher\'s evaluation.')
        report = session.scalar(select(FirstPassResult).where(FirstPassResult.submission_id == submission_id))
        return json.loads(report.report_json) if report else None


def run_first_pass(user_id: int, submission_id: int) -> dict:
    with SessionLocal() as session:
        sub = session.get(Submission, submission_id)
        if sub is None:
            raise ValueError('Submission not found.')
        assignment = session.get(Assignment, sub.assignment_id)
        if assignment is None:
            raise ValueError('Assignment not found.')
        role = _allowed_to_run(user_id, sub, assignment)
        source = read_private(sub.source_path, 'submissions')
        if assignment.rules_path:
            rules = validate_rules(read_private(assignment.rules_path, 'rules'))
        else:
            # Legacy assignments cannot grade until an instructor uploads rules.
            raise ValueError('The instructor has not provided grading rules for this assignment.')
        # If teacher did not enter a smoke input, use the first *approved*
        # PDF-generated test. Before approval, skip smoke rather than executing
        # an input-reading program with an arbitrary empty stream.
        sample_input = assignment.sample_input or None
        if sample_input is None and assignment.tests_approved and assignment.testset_json:
            approved = json.loads(assignment.testset_json)
            if approved:
                sample_input = approved[0]['input']
        report = evaluate_source(source, sample_input=sample_input,
                                 time_limit_s=rules['time_limit_s'],
                                 memory_limit_mb=rules['memory_limit_mb'],
                                 rules_hash=assignment.rules_hash)
        row = session.scalar(select(FirstPassResult).where(FirstPassResult.submission_id == submission_id))
        if not row:
            row = FirstPassResult(submission_id=submission_id)
            session.add(row)
        row.status = report['status']
        row.report_json = json.dumps(report, ensure_ascii=False)
        session.commit()
        # Detailed status, hashes, compiler logs and evidence are teacher-only.
        return report if role == 'teacher' else {'submission_id': submission_id}
