
from pathlib import Path

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker


# Locate the main project folder
PROJECT_ROOT = Path(__file__).resolve().parent.parent

# Create the data directory if it does not exist
DATA_DIR = PROJECT_ROOT / "data"
DATA_DIR.mkdir(parents=True, exist_ok=True)

# SQLite database location
DATABASE_PATH = DATA_DIR / "autograder.db"

DATABASE_URL = f"sqlite:///{DATABASE_PATH}"


# Connect SQLAlchemy to SQLite
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)


# Factory for creating database sessions
SessionLocal = sessionmaker(
    bind=engine,
    autoflush=False,
    autocommit=False,
)


# Base class used by our database tables
class Base(DeclarativeBase):
    pass
