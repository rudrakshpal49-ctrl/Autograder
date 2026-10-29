"""Teacher-managed assignment documents, deadlines and private grading rubrics."""
from datetime import datetime
from pathlib import Path
from sqlalchemy import select
from database.db import SessionLocal
from database.orm_models import Assignment
from services.authentication import require_role
from services.dates import to_utc_naive, utc_now_naive
from services.rules_service import validate_rules, automatic_rules_bytes
from services.storage_utils import validate_document, write_private, read_private, discard_private


def _as_dict(a):
    return {
        'id': a.id, 'title': a.title, 'description': a.description,
        'created_by': a.created_by, 'created_at': a.created_at,
        'deadline_at': a.deadline_at,
        'document_path': a.document_path, 'document_name': a.document_name,
        'rules_path': a.rules_path, 'rules_name': a.rules_name,
        'rules_hash': a.rules_hash,
        'sample_input': a.sample_input, 'sample_output': a.sample_output,
    }


def _validate_sample(sample_input, sample_output=None):
    if sample_input is not None and (not isinstance(sample_input, str) or len(sample_input.encode('utf-8')) > 8192):
        raise ValueError('Sample input must be UTF-8 text up to 8 KiB.')
    if sample_output is not None and (not isinstance(sample_output, str) or len(sample_output.encode('utf-8')) > 8192):
        raise ValueError('Sample output must be UTF-8 text up to 8 KiB.')
    if sample_output and not sample_input:
        raise ValueError('Provide sample stdin when giving sample stdout.')


def _check_title(title):
    title = title.strip()
    if not title or len(title) > 200:
        raise ValueError('Assignment title must be between 1 and 200 characters.')
    return title


def _check_deadline(deadline: datetime, *, allow_past=False):
    if not isinstance(deadline, datetime):
        raise ValueError('Choose a valid assignment deadline.')
    result = to_utc_naive(deadline)
    if not allow_past and result <= utc_now_naive():
        raise ValueError('The deadline must be in the future.')
    return result


def create_assignment(teacher_id: int, title: str, description: str, deadline: datetime,
                      document_name: str, document_bytes: bytes, rules_name: str | None = None, rules_bytes: bytes | None = None,
                      sample_input: str | None = None, sample_output: str | None = None):
    require_role(teacher_id, {'teacher'})
    title = _check_title(title)
    due = _check_deadline(deadline)
    if len(description) > 5000:
        raise ValueError('Instructions must be under 5,000 characters.')
    _validate_sample(sample_input, sample_output)
    validate_document(document_name, document_bytes)
    if (rules_name is None) != (rules_bytes is None):
        raise ValueError('Provide both a rules filename and its contents, or neither.')
    if rules_bytes is None:
        rules_name, rules_bytes = 'automatic_grading_rules.json', automatic_rules_bytes()
    if Path(rules_name).suffix.lower() != '.json':
        raise ValueError('The grading rules file must have a .json extension.')
    validate_rules(rules_bytes)
    doc_path = rules_path = None
    try:
        doc_path, _ = write_private('assignments', document_name, document_bytes)
        rules_path, rules_hash = write_private('rules', rules_name, rules_bytes)
        with SessionLocal() as session:
            a = Assignment(title=title, description=description.strip() or f'See {Path(document_name).name}',
                           created_by=teacher_id, deadline_at=due, document_path=doc_path,
                           document_name=Path(document_name).name,
                           rules_path=rules_path, rules_name=Path(rules_name).name, rules_hash=rules_hash,
                           sample_input=sample_input, sample_output=sample_output)
            session.add(a)
            session.commit()
            session.refresh(a)
            return a.id
    except Exception:
        discard_private(doc_path, 'assignments')
        discard_private(rules_path, 'rules')
        raise


def get_all_assignments(teacher_id=None):
    with SessionLocal() as session:
        query = select(Assignment)
        if teacher_id is not None:
            require_role(teacher_id, {'teacher'})
            query = query.where(Assignment.created_by == teacher_id)
        records = [_as_dict(a) for a in session.scalars(query.order_by(Assignment.id.desc())).all()]
        if teacher_id is None:
            # Student assignment listings must never expose rubric paths or hashes.
            for record in records:
                for private in ('rules_path', 'rules_name', 'rules_hash', 'sample_input', 'sample_output'):
                    record.pop(private, None)
        return records


def update_assignment(teacher_id: int, assignment_id: int, title: str, description: str,
                      deadline: datetime, document_name=None, document_bytes=None,
                      rules_name=None, rules_bytes=None, sample_input: str | None = None,
                      sample_output: str | None = None):
    require_role(teacher_id, {'teacher'})
    title = _check_title(title)
    due = _check_deadline(deadline, allow_past=True)
    if len(description) > 5000:
        raise ValueError('Instructions must be under 5,000 characters.')
    _validate_sample(sample_input, sample_output)
    if (document_name is None) != (document_bytes is None):
        raise ValueError('Provide a complete replacement document.')
    if (rules_name is None) != (rules_bytes is None):
        raise ValueError('Provide a complete replacement rules file.')
    if document_name is not None:
        validate_document(document_name, document_bytes)
    if rules_name is not None:
        if Path(rules_name).suffix.lower() != '.json':
            raise ValueError('Rules must be a JSON file.')
        validate_rules(rules_bytes)
    new_doc = new_rules = None
    try:
        with SessionLocal() as session:
            a = session.get(Assignment, assignment_id)
            if not a or a.created_by != teacher_id:
                raise PermissionError('This assignment is not available for editing.')
            if document_name is not None:
                new_doc, _ = write_private('assignments', document_name, document_bytes)
                a.document_path = new_doc
                a.document_name = Path(document_name).name
            if rules_name is not None:
                new_rules, hash_value = write_private('rules', rules_name, rules_bytes)
                a.rules_path = new_rules
                a.rules_name = Path(rules_name).name
                a.rules_hash = hash_value
            # If the problem or rubric changes, previously approved tests need review.
            if (new_doc is not None or new_rules is not None or title != a.title or
                    description.strip() != a.description or
                    sample_input != a.sample_input or sample_output != a.sample_output):
                a.tests_approved = False
                a.testset_json = None
                a.testset_hash = None
                a.input_contract_json = None
                a.input_contract_hash = None
            a.title = title
            a.description = description.strip() or (a.document_name or 'Assignment details')
            a.deadline_at = due
            a.sample_input = sample_input
            a.sample_output = sample_output
            session.commit()
            # Older files are intentionally retained, preserving previous rubric versions.
        return True
    except Exception:
        discard_private(new_doc, 'assignments')
        discard_private(new_rules, 'rules')
        raise


def download_assignment_document(assignment_id: int, requesting_user_id: int):
    user = require_role(requesting_user_id, {'teacher', 'student'})
    with SessionLocal() as session:
        a = session.get(Assignment, assignment_id)
        if a is None or (user['role'] == 'teacher' and a.created_by != requesting_user_id):
            raise PermissionError('Assignment not available.')
        if not a.document_path:
            raise FileNotFoundError('This older assignment does not have an uploaded document.')
        return read_private(a.document_path, 'assignments')


def download_assignment_rules(assignment_id: int, teacher_id: int):
    require_role(teacher_id, {'teacher'})
    with SessionLocal() as session:
        a = session.get(Assignment, assignment_id)
        if not a or a.created_by != teacher_id:
            raise PermissionError('Assignment rules are private to the teacher.')
        if not a.rules_path:
            raise FileNotFoundError('No rules uploaded for this assignment.')
        return read_private(a.rules_path, 'rules')
