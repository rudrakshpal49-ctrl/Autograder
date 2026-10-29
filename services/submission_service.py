"""Student source upload and teacher submission review, no execution yet."""
import hashlib
from sqlalchemy import select, delete
from database.db import SessionLocal
from database.orm_models import Assignment, Submission, User, FirstPassResult, CorrectnessResult, EasyAnalyzerResult
from services.authentication import require_role
from services.dates import utc_now_naive
from services.storage_utils import write_private, read_private, discard_private

MAX_SOURCE_SIZE = 100 * 1024


def save_submission(student_id, assignment_id, filename, source_bytes):
    require_role(student_id, {'student'})
    if not isinstance(filename, str) or not filename.lower().endswith('.c'):
        raise ValueError('Only C source files (.c) can be submitted.')
    if not isinstance(source_bytes, bytes) or not source_bytes or len(source_bytes) > MAX_SOURCE_SIZE:
        raise ValueError('C source must be nonempty and no larger than 100 KiB.')
    if b'\x00' in source_bytes:
        raise ValueError('C source contains invalid NUL bytes.')
    try:
        source_bytes.decode('utf-8')
    except UnicodeDecodeError as exc:
        raise ValueError('C source must use UTF-8 encoding.') from exc
    with SessionLocal() as session:
        assignment = session.get(Assignment, assignment_id)
        if not assignment:
            raise ValueError('Assignment not found.')
        if assignment.deadline_at and assignment.deadline_at < utc_now_naive():
            raise ValueError('The deadline has passed. New submissions are closed.')
        path = None
        try:
            path, source_hash = write_private('submissions', filename, source_bytes)
            row = Submission(student_id=student_id, assignment_id=assignment_id,
                             source_path=path, source_hash=source_hash,
                             original_filename=filename.rsplit('/', 1)[-1].rsplit('\\', 1)[-1])
            session.add(row)
            session.commit()
            session.refresh(row)
            # Internal hashes and storage paths must not be returned to students.
            return {'submission_id': row.id, 'assignment_id': assignment_id}
        except Exception:
            session.rollback()
            discard_private(path, 'submissions')
            raise


def get_student_submissions(student_id):
    require_role(student_id, {'student'})
    with SessionLocal() as session:
        result = session.execute(select(Submission, Assignment.title).join(
            Assignment, Submission.assignment_id == Assignment.id).where(
            Submission.student_id == student_id).order_by(Submission.id.desc())).all()
        return [{'id': s.id, 'assignment_id': s.assignment_id, 'title': title,
                 'created_at': s.created_at,
                 'filename': s.original_filename or 'source.c'} for s, title in result]


def get_teacher_submissions(teacher_id, assignment_id=None, student_id=None):
    require_role(teacher_id, {'teacher'})
    with SessionLocal() as session:
        stmt = (select(Submission, Assignment.title, User.username, User.full_name)
                .join(Assignment, Assignment.id == Submission.assignment_id)
                .join(User, User.id == Submission.student_id)
                .where(Assignment.created_by == teacher_id))
        if assignment_id is not None:
            stmt = stmt.where(Submission.assignment_id == assignment_id)
        if student_id is not None:
            stmt = stmt.where(Submission.student_id == student_id)
        rows = session.execute(stmt.order_by(Submission.id.desc())).all()
        return [{'id': s.id, 'assignment_id': s.assignment_id, 'assignment_title': title,
                 'student_id': s.student_id, 'student_username': username,
                 'student_name': name or username, 'source_hash': s.source_hash,
                 'filename': s.original_filename or 'source.c', 'created_at': s.created_at}
                for s, title, username, name in rows]


def get_submission_source(teacher_id, submission_id):
    require_role(teacher_id, {'teacher'})
    with SessionLocal() as session:
        row = session.get(Submission, submission_id)
        if not row:
            raise ValueError('Submission not found.')
        assignment = session.get(Assignment, row.assignment_id)
        if not assignment or assignment.created_by != teacher_id:
            raise PermissionError('You cannot access this submission.')
        return read_private(row.source_path, 'submissions')


def delete_submission(teacher_id, submission_id):
    require_role(teacher_id, {'teacher'})
    with SessionLocal() as session:
        row = session.get(Submission, submission_id)
        if not row:
            raise ValueError('Submission not found.')
        assignment = session.get(Assignment, row.assignment_id)
        if not assignment or assignment.created_by != teacher_id:
            raise PermissionError('You cannot delete this submission.')
        path = row.source_path
        session.execute(delete(FirstPassResult).where(FirstPassResult.submission_id == submission_id))
        session.execute(delete(CorrectnessResult).where(CorrectnessResult.submission_id == submission_id))
        session.execute(delete(EasyAnalyzerResult).where(EasyAnalyzerResult.submission_id == submission_id))
        session.delete(row)
        session.commit()
    discard_private(path, 'submissions')
