"""One-time database initialization.

Creates all tables, PostgreSQL enum types, and indexes defined by the
SQLAlchemy models. Safe to run repeatedly: existing objects are skipped.
"""

import app.models  # noqa: F401  (registers every model on Base.metadata)
from app.db.base import Base
from app.db.session import engine


def init_db() -> None:
    Base.metadata.create_all(bind=engine)
    print("Database initialized: all tables, enums, and indexes created.")


if __name__ == "__main__":
    init_db()
