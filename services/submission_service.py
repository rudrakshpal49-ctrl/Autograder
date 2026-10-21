
import hashlib
import uuid
from pathlib import Path

from sqlalchemy import select

from database.db import SessionLocal
from database.orm_models import Assignment, Submission
from services.authentication import require_role


# Maximum upload size: 100 KiB
MAX_SOURCE_SIZE = 100 * 1024

# Project-local submission storage
PROJECT_ROOT = Path(__file__).resolve().parent.parent
SUBMISSION_DIR = PROJECT_ROOT / "storage" / "submissions"


def save_submission(
    student_id: int,
    assignment_id: int,
    filename: str,
    source_bytes: bytes,
):
    """
    Validate and save a student C program.

    This function only stores the source code.
    It does NOT compile or execute the uploaded program.
    """

    # 1. Backend authorization
    require_role(student_id, {"student"})

    # 2. Validate the original filename
    if not isinstance(filename, str):
        raise ValueError("Invalid filename.")

    if not filename.lower().endswith(".c"):
        raise ValueError("Only .c source files are allowed.")

    # 3. Validate the source data
    if not isinstance(source_bytes, bytes):
        raise ValueError("Invalid uploaded file data.")

    if not source_bytes:
        raise ValueError("The uploaded C file is empty.")

    if len(source_bytes) > MAX_SOURCE_SIZE:
        raise ValueError(
            "The C file must not exceed 100 KiB."
        )

    if b"\x00" in source_bytes:
        raise ValueError(
            "The uploaded file contains invalid null bytes."
        )

    try:
        source_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError(
            "Please upload a UTF-8 encoded C source file."
        ) from error

    # 4. Calculate a reproducible SHA-256 source hash
    source_hash = hashlib.sha256(source_bytes).hexdigest()

    # 5. Create a unique internal filename
    stored_filename = f"{uuid.uuid4().hex}.c"

    SUBMISSION_DIR.mkdir(
        parents=True,
        exist_ok=True,
    )

    destination = SUBMISSION_DIR / stored_filename

    # 6. Verify the assignment and save its submission record
    with SessionLocal() as session:
        assignment = session.scalar(
            select(Assignment).where(
                Assignment.id == assignment_id
            )
        )

        if assignment is None:
            raise ValueError(
                "The selected assignment does not exist."
            )

        file_created = False

        try:
            # Exclusive creation: never overwrite an existing file.
            with destination.open("xb") as output_file:
                output_file.write(source_bytes)

            file_created = True

            submission = Submission(
                student_id=student_id,
                assignment_id=assignment_id,
                source_path=str(destination),
                source_hash=source_hash,
            )

            session.add(submission)
            session.commit()
            session.refresh(submission)

            return {
                "submission_id": submission.id,
                "assignment_id": assignment_id,
                "source_hash": source_hash,
                "source_path": str(destination),
            }

        except Exception:
            session.rollback()

            # Clean up the file if the database operation failed.
            if file_created:
                destination.unlink(missing_ok=True)

            raise


def get_student_submissions(student_id: int):
    """Retrieve saved submissions belonging to one student."""

    require_role(student_id, {"student"})

    with SessionLocal() as session:
        records = session.scalars(
            select(Submission)
            .where(Submission.student_id == student_id)
            .order_by(Submission.id.desc())
        ).all()

        return [
            {
                "id": record.id,
                "assignment_id": record.assignment_id,
                "source_hash": record.source_hash,
                "created_at": record.created_at,
            }
            for record in records
        ]
