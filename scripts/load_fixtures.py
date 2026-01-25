"""Load fixture data into the database.

Usage:
    python -m scripts.load_fixtures [--seed-file PATH]

This script loads the seed_minimal.sql fixture data into the database.
Useful for setting up a development or test environment.
"""

from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession

from payroll_engine.config import settings


DEFAULT_SEED_FILE = Path(__file__).parent.parent / "phase1_pack_additions" / "fixtures" / "seed_minimal.sql"


async def load_fixtures(seed_file: Path, database_url: str) -> None:
    """Load fixture SQL file into the database."""
    if not seed_file.exists():
        print(f"Error: Seed file not found: {seed_file}")
        sys.exit(1)

    print(f"Loading fixtures from: {seed_file}")
    print(f"Target database: {database_url.split('@')[1] if '@' in database_url else database_url}")

    engine = create_async_engine(database_url, echo=False)

    try:
        async with AsyncSession(engine) as session:
            # Read the SQL file
            sql_content = seed_file.read_text(encoding="utf-8")

            # Split into individual statements
            statements = []
            current_stmt = []

            for line in sql_content.split("\n"):
                # Skip empty lines and comments at statement level
                stripped = line.strip()
                if not stripped or stripped.startswith("--"):
                    continue

                current_stmt.append(line)

                # Check if statement is complete
                if stripped.endswith(";"):
                    full_stmt = "\n".join(current_stmt)
                    statements.append(full_stmt)
                    current_stmt = []

            print(f"Found {len(statements)} SQL statements")

            # Execute each statement
            success_count = 0
            error_count = 0

            for i, stmt in enumerate(statements, 1):
                try:
                    await session.execute(text(stmt))
                    success_count += 1
                except Exception as e:
                    error_count += 1
                    print(f"  Warning: Statement {i} failed: {str(e)[:80]}")

            await session.commit()

            print(f"\nResults:")
            print(f"  Successful: {success_count}")
            print(f"  Failed: {error_count}")

            if error_count == 0:
                print("\nFixtures loaded successfully!")
            else:
                print(f"\nFixtures loaded with {error_count} warnings.")

    finally:
        await engine.dispose()


def main() -> None:
    """Main entry point."""
    parser = argparse.ArgumentParser(description="Load fixture data into the database")
    parser.add_argument(
        "--seed-file",
        type=Path,
        default=DEFAULT_SEED_FILE,
        help=f"Path to seed SQL file (default: {DEFAULT_SEED_FILE})",
    )
    parser.add_argument(
        "--database-url",
        type=str,
        default=settings.database_url,
        help="Database URL (default: from settings)",
    )

    args = parser.parse_args()

    asyncio.run(load_fixtures(args.seed_file, args.database_url))


if __name__ == "__main__":
    main()
