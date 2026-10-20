
from sqlalchemy import select

from database.db import SessionLocal
from database.orm_models import Assignment
from services.authentication import require_role


def create_assignment(
    teacher_id: int,
    title: str,
    description: str,
):
    """Create an assignment after verifying teacher permissions."""

    # Security: students cannot create assignments.
    require_role(teacher_id, {"teacher"})

    title = title.strip()
    description = description.strip()

    if not title:
        raise ValueError("Assignment title cannot be empty.")

    if not description:
        raise ValueError("Problem statement cannot be empty.")

    if len(title) > 200:
        raise ValueError(
            "Assignment title must not exceed 200 characters."
        )

    with SessionLocal() as session:
        assignment = Assignment(
            title=title,
            description=description,
            created_by=teacher_id,
        )

        session.add(assignment)
        session.commit()
        session.refresh(assignment)

        return assignment.id


def get_all_assignments():
    """Retrieve assignments for the dashboard."""

    with SessionLocal() as session:
        assignments = session.scalars(
            select(Assignment).order_by(Assignment.id.desc())
        ).all()

        return [
            {
                "id": assignment.id,
                "title": assignment.title,
                "description": assignment.description,
            }
            for assignment in assignments
        ]
