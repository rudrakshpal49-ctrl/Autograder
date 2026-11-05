"""Create tables and safely add missing columns to existing SQLite databases.

Unlike dropping/recreating tables, this preserves existing users, assignments,
and submissions. Run automatically when the Streamlit app starts.
"""
from sqlalchemy import inspect, text
from database.db import Base, engine
from database import orm_models  # noqa: F401  - registers the models


# SQLite-compatible additive schema changes. Existing rows get safe defaults.
ADDITIONAL_COLUMNS = {
    'users': {
        'full_name': 'VARCHAR(150)',
        'student_number': 'VARCHAR(50)',
        'email': 'VARCHAR(254)',
        'is_active': 'BOOLEAN NOT NULL DEFAULT 1',
    },
    'assignments': {
        'deadline_at': 'DATETIME',
        'document_path': 'VARCHAR(500)',
        'document_name': 'VARCHAR(255)',
        'rules_path': 'VARCHAR(500)',
        'rules_name': 'VARCHAR(255)',
        'rules_hash': 'VARCHAR(64)',
        'sample_input': 'TEXT',
        'oracle_path': 'VARCHAR(500)',
        'tests_approved': 'BOOLEAN NOT NULL DEFAULT 0',  # Legacy compatibility only
        'edge_tests_approved': 'BOOLEAN NOT NULL DEFAULT 0',  # Legacy compatibility only
    },
    'grading_cases': {
        'rationale': "TEXT NOT NULL DEFAULT ''",
    },
    'submissions': {
        'original_filename': 'VARCHAR(255)',
    },
}


def initialize_database():
    Base.metadata.create_all(bind=engine)
    with engine.begin() as connection:
        for table, columns in ADDITIONAL_COLUMNS.items():
            existing = {c['name'] for c in inspect(connection).get_columns(table)}
            for column, sql_type in columns.items():
                if column not in existing:
                    connection.execute(text(f'ALTER TABLE {table} ADD COLUMN {column} {sql_type}'))


if __name__ == '__main__':
    initialize_database()
    print('AutoGrader database initialized / upgraded (existing records retained).')
