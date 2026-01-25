#!/usr/bin/env python
"""Apply PSP migrations to the database.

Usage:
    python scripts/migrate.py
    python scripts/migrate.py --database-url postgresql://...
    python scripts/migrate.py --dry-run
"""

import argparse
import os
import re
import sys
from pathlib import Path

try:
    from sqlalchemy import create_engine, text
except ImportError:
    print("ERROR: SQLAlchemy not installed. Run: pip install sqlalchemy psycopg2-binary")
    sys.exit(1)


def get_migration_files() -> list[Path]:
    """Get all migration files in order."""
    migrations_dir = Path(__file__).parent.parent / "psp_build_pack_v2" / "psp_build_pack" / "migrations"

    if not migrations_dir.exists():
        print(f"ERROR: Migrations directory not found: {migrations_dir}")
        sys.exit(1)

    files = sorted(migrations_dir.glob("*.sql"))
    return files


def parse_migration_number(filepath: Path) -> int:
    """Extract migration number from filename."""
    match = re.match(r"(\d+)", filepath.name)
    if match:
        return int(match.group(1))
    return 0


def get_applied_migrations(engine) -> set[int]:
    """Get set of applied migration numbers."""
    with engine.connect() as conn:
        # Create tracking table if not exists
        conn.execute(text("""
            CREATE TABLE IF NOT EXISTS psp_migration_history (
                migration_number INT PRIMARY KEY,
                filename TEXT NOT NULL,
                applied_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
        """))
        conn.commit()

        result = conn.execute(text("SELECT migration_number FROM psp_migration_history"))
        return {row[0] for row in result}


def apply_migration(engine, filepath: Path, migration_num: int, dry_run: bool) -> bool:
    """Apply a single migration file."""
    print(f"  Applying: {filepath.name}")

    sql_content = filepath.read_text(encoding="utf-8")

    if dry_run:
        print(f"    [DRY RUN] Would execute {len(sql_content)} characters")
        return True

    try:
        with engine.connect() as conn:
            # Execute the migration
            conn.execute(text(sql_content))

            # Record in history
            conn.execute(
                text("""
                    INSERT INTO psp_migration_history (migration_number, filename)
                    VALUES (:num, :name)
                    ON CONFLICT (migration_number) DO NOTHING
                """),
                {"num": migration_num, "name": filepath.name},
            )
            conn.commit()

        print(f"    OK")
        return True

    except Exception as e:
        print(f"    FAILED: {e}")
        return False


def main() -> int:
    parser = argparse.ArgumentParser(description="Apply PSP migrations")
    parser.add_argument(
        "--database-url",
        default=os.environ.get("DATABASE_URL", "postgresql://payroll:payroll_dev@localhost/payroll_dev"),
        help="Database URL",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be applied without executing",
    )

    args = parser.parse_args()

    print("PSP Migration Runner")
    print("=" * 50)
    print(f"Database: {args.database_url.split('@')[-1] if '@' in args.database_url else args.database_url}")
    print()

    # Get migration files
    migration_files = get_migration_files()
    print(f"Found {len(migration_files)} migration files")

    # Connect and get applied migrations
    engine = create_engine(args.database_url)

    try:
        applied = get_applied_migrations(engine)
        print(f"Already applied: {len(applied)}")
        print()
    except Exception as e:
        print(f"ERROR: Could not connect to database: {e}")
        return 1

    # Apply pending migrations
    pending = []
    for filepath in migration_files:
        migration_num = parse_migration_number(filepath)
        if migration_num not in applied:
            pending.append((migration_num, filepath))

    if not pending:
        print("No pending migrations.")
        return 0

    print(f"Pending migrations: {len(pending)}")
    print()

    success_count = 0
    fail_count = 0

    for migration_num, filepath in sorted(pending):
        if apply_migration(engine, filepath, migration_num, args.dry_run):
            success_count += 1
        else:
            fail_count += 1
            print("\nStopping due to failure.")
            break

    print()
    print("=" * 50)
    print(f"Applied: {success_count}, Failed: {fail_count}")

    return 0 if fail_count == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
