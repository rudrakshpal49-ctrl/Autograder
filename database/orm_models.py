"""SQLAlchemy models. Dates are stored in UTC (naive values for SQLite)."""
from datetime import datetime, timezone
from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column
from database.db import Base


def utc_now():
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = 'users'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    username: Mapped[str] = mapped_column(String(100), unique=True, nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False)
    full_name: Mapped[str | None] = mapped_column(String(150), nullable=True)
    student_number: Mapped[str | None] = mapped_column(String(50), nullable=True)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)


class Assignment(Base):
    __tablename__ = 'assignments'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    created_by: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=utc_now)
    deadline_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=False), nullable=True)
    document_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    document_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rules_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    rules_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    rules_hash: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sample_input: Mapped[str | None] = mapped_column(Text, nullable=True)
    oracle_path: Mapped[str | None] = mapped_column(String(500), nullable=True)
    # Legacy schema compatibility: old AutoGrader databases have these
    # NOT NULL columns with no SQL-level defaults. Keep inert values so that
    # new assignment inserts succeed; no grading analyzers use them.
    tests_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)
    edge_tests_approved: Mapped[bool] = mapped_column(Boolean, default=False, nullable=False)


class Submission(Base):
    __tablename__ = 'submissions'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    student_id: Mapped[int] = mapped_column(ForeignKey('users.id'), nullable=False)
    assignment_id: Mapped[int] = mapped_column(ForeignKey('assignments.id'), nullable=False)
    source_path: Mapped[str] = mapped_column(String(500), nullable=False)
    source_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    original_filename: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=utc_now)


class FirstPassResult(Base):
    __tablename__ = 'first_pass_results'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    submission_id: Mapped[int] = mapped_column(ForeignKey('submissions.id', ondelete='CASCADE'), nullable=False, unique=True)
    status: Mapped[str] = mapped_column(String(30), nullable=False)
    report_json: Mapped[str] = mapped_column(Text, nullable=False)
    evaluated_at: Mapped[datetime] = mapped_column(DateTime(timezone=False), default=utc_now, onupdate=utc_now)



class GradingCase(Base):
    """One assignment-scoped test case; AI drafts never count until approved."""
    __tablename__ = 'grading_cases'
    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    assignment_id: Mapped[int] = mapped_column(ForeignKey('assignments.id', ondelete='CASCADE'), nullable=False)
    category: Mapped[str] = mapped_column(String(15), nullable=False)  # normal / edge
    source: Mapped[str] = mapped_column(String(15), nullable=False)  # teacher / qwen
    title: Mapped[str] = mapped_column(String(100), nullable=False, default='')
    rationale: Mapped[str] = mapped_column(Text, nullable=False, default='')
    stdin: Mapped[str] = mapped_column(Text, nullable=False)
    expected_output: Mapped[str] = mapped_column(Text, nullable=False)
    approved: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    oracle_verified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
