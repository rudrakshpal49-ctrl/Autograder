
from argon2 import PasswordHasher
from argon2.exceptions import VerifyMismatchError, VerificationError
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from database.db import SessionLocal
from database.orm_models import User


password_hasher = PasswordHasher()

VALID_ROLES = {"student", "teacher"}


def create_user(username: str, password: str, role: str):
    """Create a student or teacher account."""

    username = username.strip()

    if not username:
        raise ValueError("Username cannot be empty.")

    if len(password) < 12:
        raise ValueError(
            "Password must contain at least 12 characters."
        )

    if role not in VALID_ROLES:
        raise ValueError("Invalid account role.")

    with SessionLocal() as session:

        existing_user = session.scalar(
            select(User).where(User.username == username)
        )

        if existing_user:
            raise ValueError("Username already exists.")

        user = User(
            username=username,
            password_hash=password_hasher.hash(password),
            role=role,
        )

        session.add(user)

        try:
            session.commit()
            session.refresh(user)

        except IntegrityError:
            session.rollback()
            raise ValueError("Username already exists.")

        return user.id


def authenticate_user(username: str, password: str):
    """Verify login credentials."""

    username = username.strip()

    with SessionLocal() as session:

        user = session.scalar(
            select(User).where(User.username == username)
        )

        if user is None:
            return None

        if not user.is_active:
            return None

        try:
            password_hasher.verify(
                user.password_hash,
                password,
            )

        except (VerifyMismatchError, VerificationError):
            return None

        if password_hasher.check_needs_rehash(
            user.password_hash
        ):
            user.password_hash = password_hasher.hash(password)
            session.commit()

        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "full_name": user.full_name or user.username,
            "is_active": bool(user.is_active),
        }


def get_user_by_id(user_id: int):
    """Retrieve user information."""

    with SessionLocal() as session:

        user = session.get(User, user_id)

        if user is None or not user.is_active:
            return None

        return {
            "id": user.id,
            "username": user.username,
            "role": user.role,
            "full_name": user.full_name or user.username,
            "is_active": bool(user.is_active),
        }


def require_role(user_id: int, allowed_roles: set[str]):
    """Check backend permissions."""

    user = get_user_by_id(user_id)

    if user is None or user["role"] not in allowed_roles:
        raise PermissionError(
            "You are not authorized to perform this action."
        )

    return user
