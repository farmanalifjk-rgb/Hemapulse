"""Database initialization from schema.sql.

Executes the statements in ``schema.sql`` (the authoritative schema) against
the database referenced by DATABASE_URL, in a single transaction. The SQL text
is used exactly as written; no SQLAlchemy models are involved.

schema.sql uses plain CREATE TYPE / CREATE TABLE / CREATE INDEX statements, so
to be idempotent each statement runs inside a savepoint and is skipped when the
object already exists. Any other error rolls back the whole transaction.

Usage: python init_db.py
"""

import os
import re
import sys
from pathlib import Path

import psycopg
from psycopg import errors

SCHEMA_PATH = Path(__file__).resolve().parent / "schema.sql"

# SQLSTATE classes meaning "object already exists".
ALREADY_EXISTS = (
    errors.DuplicateObject,  # 42710: types, constraints, extensions
    errors.DuplicateTable,  # 42P07: tables, indexes, sequences
)


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        print("ERROR: DATABASE_URL environment variable is not set.")
        sys.exit(1)
    # Accept SQLAlchemy-style URLs such as postgresql+psycopg://
    return re.sub(r"^(postgres(?:ql)?)\+\w+://", r"\1://", url)


def split_statements(sql: str) -> list[str]:
    """Split SQL text into statements on top-level semicolons.

    Respects single-quoted strings, double-quoted identifiers, and ``--`` line
    comments. Statement text is preserved verbatim (minus leading comment-only
    lines and surrounding whitespace).
    """
    statements: list[str] = []
    buf: list[str] = []
    i, n = 0, len(sql)
    quote = None

    while i < n:
        ch = sql[i]
        if quote:
            buf.append(ch)
            if ch == quote:
                if i + 1 < n and sql[i + 1] == quote:  # escaped quote
                    buf.append(sql[i + 1])
                    i += 1
                else:
                    quote = None
        elif ch in ("'", '"'):
            quote = ch
            buf.append(ch)
        elif ch == "-" and sql.startswith("--", i):
            end = sql.find("\n", i)
            end = n if end == -1 else end
            buf.append(sql[i:end])
            i = end - 1
        elif ch == ";":
            statements.append("".join(buf))
            buf = []
        else:
            buf.append(ch)
        i += 1

    statements.append("".join(buf))

    cleaned = []
    for stmt in statements:
        lines = stmt.strip().splitlines()
        # Drop leading comment/blank lines so the statement starts at its SQL.
        while lines and (not lines[0].strip() or lines[0].lstrip().startswith("--")):
            lines.pop(0)
        text = "\n".join(lines).strip()
        if text:
            cleaned.append(text)
    return cleaned


def describe(stmt: str) -> str:
    return " ".join(stmt.split())[:80]


def main() -> int:
    if not SCHEMA_PATH.is_file():
        print(f"ERROR: schema file not found: {SCHEMA_PATH}")
        return 1

    statements = split_statements(SCHEMA_PATH.read_text(encoding="utf-8"))
    print(f"Read {len(statements)} statements from {SCHEMA_PATH.name}")

    expected_tables = [
        m.group(1)
        for s in statements
        if (m := re.match(r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?(\w+)", s, re.I))
    ]

    url = get_database_url()
    created = skipped = 0

    try:
        with psycopg.connect(url, options="-csearch_path=public") as conn:
            print("Connected to PostgreSQL.")

            # The connection context manager commits on success and rolls back
            # on exception, so the whole file is one transaction.
            for stmt in statements:
                label = describe(stmt)
                is_extension = re.match(r"CREATE\s+EXTENSION", stmt, re.I) is not None
                try:
                    with conn.transaction():  # savepoint
                        conn.execute(stmt)
                    created += 1
                    print(f"  OK       {label}")
                except ALREADY_EXISTS as exc:
                    skipped += 1
                    print(f"  SKIPPED  {label}  (already exists: {exc.diag.message_primary})")
                except psycopg.Error as exc:
                    if is_extension:
                        skipped += 1
                        print(f"  WARNING  {label}  (could not create extension: {exc})")
                        continue
                    print(f"  ERROR    {label}\n           {exc}")
                    raise

            with conn.cursor() as cur:
                cur.execute(
                    "SELECT table_name FROM information_schema.tables "
                    "WHERE table_schema = 'public' AND table_type = 'BASE TABLE'"
                )
                existing = {row[0] for row in cur.fetchall()}

            present = [t for t in expected_tables if t in existing]
            missing = [t for t in expected_tables if t not in existing]
            print(
                f"Statements executed: {created}, skipped: {skipped}. "
                f"Tables created/verified: {len(present)}/{len(expected_tables)}."
            )
            if missing:
                print(f"ERROR: missing tables: {', '.join(missing)}")
                raise RuntimeError("schema verification failed")
    except Exception as exc:
        print(f"Database initialization failed; transaction rolled back: {exc}")
        return 1

    print("Database initialization complete.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
