"""Teacher-only student profile management (create, edit, deactivate, delete)."""
import re
from argon2 import PasswordHasher
from sqlalchemy import select, func
from sqlalchemy.exc import IntegrityError
from database.db import SessionLocal
from database.orm_models import User, Submission
from services.authentication import require_role
from services.storage_utils import discard_private
from services.submission_service import delete_submission_reports

_hasher = PasswordHasher()


def _check_details(username, full_name, student_number, email):
    username, full_name = username.strip(), full_name.strip()
    student_number, email = student_number.strip(), email.strip()
    if not username or len(username) > 100:
        raise ValueError('Username is required and must be at most 100 characters.')
    if not full_name or len(full_name) > 150:
        raise ValueError('Student full name is required and must be at most 150 characters.')
    if len(student_number) > 50:
        raise ValueError('Student ID is too long.')
    if email and (len(email) > 254 or not re.fullmatch(r'[^\s@]+@[^\s@]+\.[^\s@]+', email)):
        raise ValueError('Enter a valid email address or leave it blank.')
    return username, full_name, student_number, email


def create_student(teacher_id, username, password, full_name, student_number='', email=''):
    require_role(teacher_id, {'teacher'})
    username, full_name, student_number, email = _check_details(username, full_name, student_number, email)
    if len(password) < 6:
        raise ValueError('Password must contain at least 6 characters.')
    with SessionLocal() as session:
        if student_number and session.scalar(select(User.id).where(User.student_number == student_number)):
            raise ValueError('This student ID already exists.')
        if session.scalar(select(User.id).where(User.username == username)):
            raise ValueError('Username already exists.')
        user = User(username=username, password_hash=_hasher.hash(password), role='student',
                    full_name=full_name, student_number=student_number or None,
                    email=email or None, is_active=True)
        session.add(user)
        try:
            session.commit()
            session.refresh(user)
        except IntegrityError as exc:
            session.rollback()
            raise ValueError('Username already exists.') from exc
        return user.id


def list_students(teacher_id):
    require_role(teacher_id, {'teacher'})
    with SessionLocal() as session:
        students = session.scalars(select(User).where(User.role == 'student').order_by(User.id.desc())).all()
        counts = dict(session.execute(select(Submission.student_id, func.count(Submission.id)).group_by(Submission.student_id)).all())
        return [
            {'id': u.id, 'username': u.username, 'full_name': u.full_name or '',
             'student_number': u.student_number or '', 'email': u.email or '',
             'is_active': bool(u.is_active), 'submission_count': counts.get(u.id, 0)}
            for u in students
        ]


def update_student(teacher_id, student_id, username, full_name, student_number='',
                   email='', is_active=True, new_password=''):
    require_role(teacher_id, {'teacher'})
    username, full_name, student_number, email = _check_details(username, full_name, student_number, email)
    if new_password and len(new_password) < 6:
        raise ValueError('A new password must contain at least 6 characters.')
    with SessionLocal() as session:
        user = session.get(User, student_id)
        if not user or user.role != 'student':
            raise ValueError('Student account not found.')
        if session.scalar(select(User.id).where(User.username == username, User.id != student_id)):
            raise ValueError('Username already exists.')
        if student_number and session.scalar(select(User.id).where(User.student_number == student_number, User.id != student_id)):
            raise ValueError('Student ID already exists.')
        user.username = username
        user.full_name = full_name
        user.student_number = student_number or None
        user.email = email or None
        user.is_active = bool(is_active)
        if new_password:
            user.password_hash = _hasher.hash(new_password)
        try:
            session.commit()
        except IntegrityError as exc:
            session.rollback()
            raise ValueError('A profile with these details already exists.') from exc


def delete_student(teacher_id, student_id):
    """Permanently delete a student and their source submissions; require UI confirmation."""
    require_role(teacher_id, {'teacher'})
    with SessionLocal() as session:
        user = session.get(User, student_id)
        if not user or user.role != 'student':
            raise ValueError('Student account not found.')
        submissions = session.scalars(select(Submission).where(Submission.student_id == student_id)).all()
        paths = [row.source_path for row in submissions]
        for row in submissions:
            delete_submission_reports(session, row.id)
            session.delete(row)
        session.delete(user)
        session.commit()
    for path in paths:
        discard_private(path, 'submissions')
