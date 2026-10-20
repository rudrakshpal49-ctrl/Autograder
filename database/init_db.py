
from sqlalchemy import inspect

from database.db import Base, engine

# Import models to register our tables
from database import orm_models


def initialize_database():
    Base.metadata.create_all(bind=engine)

    inspector = inspect(engine)

    print("AutoGrader database initialized!")
    print("Available tables:")

    for table_name in inspector.get_table_names():
        print(f" - {table_name}")


if __name__ == "__main__":
    initialize_database()
