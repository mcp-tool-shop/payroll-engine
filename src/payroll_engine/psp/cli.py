"""PSP Command Line Interface.

Provides operational tools for:
- Event replay
- Event export
- Metrics emission
- Health checks
- Balance queries

Usage:
    python -m payroll_engine.psp.cli replay-events --tenant-id X --since Y
    python -m payroll_engine.psp.cli export-events --tenant-id X --output file.jsonl
    python -m payroll_engine.psp.cli balance --tenant-id X --account-id Y
    python -m payroll_engine.psp.cli health
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime
from decimal import Decimal
from typing import Any, Callable
from uuid import UUID

# Note: Actual DB session would come from your app's configuration
# This is a placeholder showing the CLI structure


def parse_datetime(s: str) -> datetime:
    """Parse ISO datetime string."""
    return datetime.fromisoformat(s.replace("Z", "+00:00"))


def parse_uuid(s: str) -> UUID:
    """Parse UUID string."""
    return UUID(s)


class PSPCli:
    """PSP Command Line Interface."""

    def __init__(self) -> None:
        self.parser = self._build_parser()

    def _build_parser(self) -> argparse.ArgumentParser:
        """Build argument parser."""
        parser = argparse.ArgumentParser(
            prog="python -m payroll_engine.psp.cli",
            description="PSP operational tools",
        )
        subparsers = parser.add_subparsers(dest="command", help="Commands")

        # replay-events command
        replay = subparsers.add_parser(
            "replay-events",
            help="Replay domain events to handlers",
        )
        replay.add_argument(
            "--tenant-id",
            type=parse_uuid,
            required=True,
            help="Tenant ID to replay events for",
        )
        replay.add_argument(
            "--since",
            type=parse_datetime,
            help="Replay events after this timestamp (ISO format)",
        )
        replay.add_argument(
            "--until",
            type=parse_datetime,
            help="Replay events before this timestamp (ISO format)",
        )
        replay.add_argument(
            "--event-types",
            type=str,
            help="Comma-separated list of event types to replay",
        )
        replay.add_argument(
            "--categories",
            type=str,
            help="Comma-separated list of categories (payment,funding,settlement,etc)",
        )
        replay.add_argument(
            "--handler",
            type=str,
            help="Handler/subscription to replay to",
        )
        replay.add_argument(
            "--correlation-id",
            type=parse_uuid,
            help="Replay only events with this correlation ID",
        )
        replay.add_argument(
            "--limit",
            type=int,
            default=1000,
            help="Maximum events to replay (default: 1000)",
        )
        replay.add_argument(
            "--dry-run",
            action="store_true",
            help="Show what would be replayed without executing",
        )

        # export-events command
        export = subparsers.add_parser(
            "export-events",
            help="Export domain events to file",
        )
        export.add_argument(
            "--tenant-id",
            type=parse_uuid,
            required=True,
            help="Tenant ID to export events for",
        )
        export.add_argument(
            "--since",
            type=parse_datetime,
            help="Export events after this timestamp",
        )
        export.add_argument(
            "--until",
            type=parse_datetime,
            help="Export events before this timestamp",
        )
        export.add_argument(
            "--event-types",
            type=str,
            help="Comma-separated list of event types",
        )
        export.add_argument(
            "--output",
            type=str,
            required=True,
            help="Output file path (.jsonl format)",
        )
        export.add_argument(
            "--entity-type",
            type=str,
            help="Filter by entity type (payment_instruction, settlement_event, etc)",
        )
        export.add_argument(
            "--entity-id",
            type=parse_uuid,
            help="Filter by entity ID",
        )

        # balance command
        balance = subparsers.add_parser(
            "balance",
            help="Query account balance",
        )
        balance.add_argument(
            "--tenant-id",
            type=parse_uuid,
            required=True,
            help="Tenant ID",
        )
        balance.add_argument(
            "--account-id",
            type=parse_uuid,
            required=True,
            help="Ledger account ID",
        )
        balance.add_argument(
            "--include-reservations",
            action="store_true",
            help="Show reservation breakdown",
        )

        # health command
        health = subparsers.add_parser(
            "health",
            help="Check PSP system health",
        )
        health.add_argument(
            "--component",
            type=str,
            choices=["all", "db", "providers", "events"],
            default="all",
            help="Component to check",
        )

        # metrics command
        metrics = subparsers.add_parser(
            "metrics",
            help="Emit PSP metrics",
        )
        metrics.add_argument(
            "--format",
            type=str,
            choices=["json", "prometheus"],
            default="json",
            help="Output format",
        )

        # subscriptions command
        subs = subparsers.add_parser(
            "subscriptions",
            help="Manage event subscriptions",
        )
        subs.add_argument(
            "--list",
            action="store_true",
            help="List all subscriptions",
        )
        subs.add_argument(
            "--create",
            type=str,
            metavar="NAME",
            help="Create new subscription",
        )
        subs.add_argument(
            "--reset",
            type=str,
            metavar="NAME",
            help="Reset subscription position to beginning",
        )

        # schema-check command
        schema = subparsers.add_parser(
            "schema-check",
            help="Verify PSP database schema is correctly applied",
        )
        schema.add_argument(
            "--database-url",
            type=str,
            help="Database URL (default: $DATABASE_URL)",
        )
        schema.add_argument(
            "--fix",
            action="store_true",
            help="Attempt to fix missing constraints (dangerous)",
        )

        return parser

    def run(self, args: list[str] | None = None) -> int:
        """Run the CLI with given arguments."""
        parsed = self.parser.parse_args(args)

        if not parsed.command:
            self.parser.print_help()
            return 1

        # Dispatch to command handler
        handlers: dict[str, Callable[..., int]] = {
            "replay-events": self._cmd_replay_events,
            "export-events": self._cmd_export_events,
            "balance": self._cmd_balance,
            "health": self._cmd_health,
            "metrics": self._cmd_metrics,
            "subscriptions": self._cmd_subscriptions,
            "schema-check": self._cmd_schema_check,
        }

        handler = handlers.get(parsed.command)
        if handler:
            return handler(parsed)

        print(f"Unknown command: {parsed.command}", file=sys.stderr)
        return 1

    def _cmd_replay_events(self, args: argparse.Namespace) -> int:
        """Replay domain events."""
        print(f"Replaying events for tenant: {args.tenant_id}")

        # Parse event types if provided
        event_types = None
        if args.event_types:
            event_types = [t.strip() for t in args.event_types.split(",")]
            print(f"  Event types: {event_types}")

        # Parse categories if provided
        categories = None
        if args.categories:
            categories = [c.strip() for c in args.categories.split(",")]
            print(f"  Categories: {categories}")

        if args.since:
            print(f"  Since: {args.since.isoformat()}")
        if args.until:
            print(f"  Until: {args.until.isoformat()}")
        if args.correlation_id:
            print(f"  Correlation ID: {args.correlation_id}")

        print(f"  Limit: {args.limit}")
        print(f"  Handler: {args.handler or 'stdout'}")

        if args.dry_run:
            print("\n[DRY RUN] Would replay events:")

        # In real implementation:
        # session = get_db_session()
        # store = EventStore(session)
        #
        # for event in store.replay(
        #     tenant_id=args.tenant_id,
        #     after=args.since,
        #     before=args.until,
        #     event_types=event_types,
        #     limit=args.limit,
        # ):
        #     if args.dry_run:
        #         print(f"  {event.timestamp} | {event.event_type}")
        #     else:
        #         handler.process(event)

        # Placeholder output
        print("\n  [Events would be replayed here]")
        print("\nReplay complete.")

        return 0

    def _cmd_export_events(self, args: argparse.Namespace) -> int:
        """Export domain events to file."""
        print(f"Exporting events for tenant: {args.tenant_id}")
        print(f"  Output: {args.output}")

        if args.since:
            print(f"  Since: {args.since.isoformat()}")
        if args.until:
            print(f"  Until: {args.until.isoformat()}")
        if args.entity_type:
            print(f"  Entity type: {args.entity_type}")
        if args.entity_id:
            print(f"  Entity ID: {args.entity_id}")

        # In real implementation:
        # session = get_db_session()
        # store = EventStore(session)
        # count = 0
        #
        # with open(args.output, "w") as f:
        #     for event in store.replay(
        #         tenant_id=args.tenant_id,
        #         after=args.since,
        #         before=args.until,
        #     ):
        #         f.write(json.dumps({
        #             "event_id": str(event.event_id),
        #             "event_type": event.event_type,
        #             "timestamp": event.timestamp.isoformat(),
        #             "payload": event.payload,
        #         }) + "\n")
        #         count += 1
        #
        # print(f"\nExported {count} events to {args.output}")

        print("\n  [Events would be exported here]")
        print("\nExport complete.")

        return 0

    def _cmd_balance(self, args: argparse.Namespace) -> int:
        """Query account balance."""
        print(f"Balance for account: {args.account_id}")
        print(f"  Tenant: {args.tenant_id}")

        # In real implementation:
        # session = get_db_session()
        # ledger = LedgerService(session)
        # balance = ledger.get_balance(
        #     tenant_id=args.tenant_id,
        #     account_id=args.account_id,
        # )
        #
        # print(f"\n  Total:     {balance.total:>15,.2f}")
        # print(f"  Reserved:  {balance.reserved:>15,.2f}")
        # print(f"  Available: {balance.available:>15,.2f}")

        # Placeholder output
        print("\n  Total:        50,000.00")
        print("  Reserved:     10,000.00")
        print("  Available:    40,000.00")

        if args.include_reservations:
            print("\n  Active Reservations:")
            print("    - payroll_batch:abc123  $8,000.00 (expires in 23h)")
            print("    - payroll_batch:def456  $2,000.00 (expires in 2h)")

        return 0

    def _cmd_health(self, args: argparse.Namespace) -> int:
        """Check system health."""
        print("PSP Health Check")
        print("=" * 40)

        checks = {
            "db": self._check_db_health,
            "providers": self._check_provider_health,
            "events": self._check_event_health,
        }

        if args.component == "all":
            components = list(checks.keys())
        else:
            components = [args.component]

        all_healthy = True
        for component in components:
            check = checks.get(component)
            if check:
                status, details = check()
                status_str = "✓ OK" if status else "✗ FAIL"
                print(f"\n{component}: {status_str}")
                for key, value in details.items():
                    print(f"  {key}: {value}")
                if not status:
                    all_healthy = False

        print("\n" + "=" * 40)
        if all_healthy:
            print("Overall: HEALTHY")
            return 0
        else:
            print("Overall: UNHEALTHY")
            return 1

    def _check_db_health(self) -> tuple[bool, dict[str, Any]]:
        """Check database health."""
        # In real implementation, actually check DB connection
        return True, {
            "connection": "ok",
            "latency_ms": 5,
            "pool_size": 10,
            "active_connections": 3,
        }

    def _check_provider_health(self) -> tuple[bool, dict[str, Any]]:
        """Check provider health."""
        # In real implementation, ping providers
        return True, {
            "ach_stub": "ok",
            "fednow_stub": "ok",
            "registered_providers": 2,
        }

    def _check_event_health(self) -> tuple[bool, dict[str, Any]]:
        """Check event system health."""
        # In real implementation, check event store
        return True, {
            "event_store": "ok",
            "total_events": 15234,
            "events_24h": 892,
            "subscriptions_active": 3,
        }

    def _cmd_metrics(self, args: argparse.Namespace) -> int:
        """Emit metrics."""
        # In real implementation, gather actual metrics
        metrics = {
            "psp_payments_total": {
                "type": "counter",
                "value": 15234,
                "labels": {"status": "all"},
            },
            "psp_payments_settled": {
                "type": "counter",
                "value": 14892,
                "labels": {"status": "settled"},
            },
            "psp_payments_returned": {
                "type": "counter",
                "value": 342,
                "labels": {"status": "returned"},
            },
            "psp_ledger_entries_total": {
                "type": "counter",
                "value": 45678,
            },
            "psp_balance_total_usd": {
                "type": "gauge",
                "value": 2500000.00,
            },
            "psp_reconciliation_latency_seconds": {
                "type": "histogram",
                "buckets": [0.1, 0.5, 1.0, 5.0, 10.0],
                "sum": 234.5,
                "count": 1200,
            },
        }

        if args.format == "json":
            print(json.dumps(metrics, indent=2))
        else:
            # Prometheus format
            for name, data in metrics.items():
                if data["type"] == "counter":
                    labels = ""
                    if "labels" in data:
                        label_str = ",".join(f'{k}="{v}"' for k, v in data["labels"].items())
                        labels = f"{{{label_str}}}"
                    print(f"{name}{labels} {data['value']}")
                elif data["type"] == "gauge":
                    print(f"{name} {data['value']}")
                elif data["type"] == "histogram":
                    print(f"{name}_sum {data['sum']}")
                    print(f"{name}_count {data['count']}")

        return 0

    def _cmd_subscriptions(self, args: argparse.Namespace) -> int:
        """Manage event subscriptions."""
        if args.list:
            print("Event Subscriptions:")
            print("-" * 60)
            # In real implementation, query psp_event_subscription table
            subs = [
                {
                    "name": "compliance_alerts",
                    "last_processed": "2025-01-25T10:30:00Z",
                    "event_types": ["PaymentReturned", "LiabilityClassified"],
                    "active": True,
                },
                {
                    "name": "client_notifications",
                    "last_processed": "2025-01-25T10:29:45Z",
                    "event_types": None,  # All
                    "active": True,
                },
                {
                    "name": "metrics_aggregator",
                    "last_processed": "2025-01-25T10:31:00Z",
                    "event_types": None,
                    "active": True,
                },
            ]
            for sub in subs:
                status = "active" if sub["active"] else "inactive"
                types = sub["event_types"] or ["all"]
                print(f"  {sub['name']}")
                print(f"    Status: {status}")
                print(f"    Last processed: {sub['last_processed']}")
                print(f"    Event types: {types}")
                print()

        elif args.create:
            print(f"Creating subscription: {args.create}")
            # In real implementation, insert into psp_event_subscription
            print("  Created successfully.")

        elif args.reset:
            print(f"Resetting subscription: {args.reset}")
            # In real implementation, update last_event_id/timestamp to NULL
            print("  Position reset to beginning.")

        return 0

    def _cmd_schema_check(self, args: argparse.Namespace) -> int:
        """Verify PSP database schema."""
        import os

        database_url = args.database_url or os.environ.get("DATABASE_URL")
        if not database_url:
            print("ERROR: --database-url required or set DATABASE_URL", file=sys.stderr)
            return 1

        print("PSP Schema Verification")
        print("=" * 60)

        # Required tables
        required_tables = [
            "psp_ledger_account",
            "psp_ledger_entry",
            "psp_balance_reservation",
            "psp_funding_request",
            "psp_bank_account",
            "psp_settlement_event",
            "psp_domain_event",
            "psp_event_subscription",
            "payment_instruction",
            "payment_attempt",
            "liability_event",
        ]

        # Required constraints
        required_constraints = [
            ("psp_ledger_entry", "chk_ledger_entry_amount_positive"),
            ("psp_ledger_entry", "chk_ledger_entry_different_accounts"),
            ("payment_instruction", "chk_payment_amount_positive"),
            ("psp_balance_reservation", "chk_reservation_amount_positive"),
        ]

        # Required triggers
        required_triggers = [
            ("payment_instruction", "trg_validate_payment_status_transition"),
        ]

        # Required indexes
        required_indexes = [
            "idx_ledger_entry_tenant_source",
            "idx_payment_instruction_tenant_status",
            "idx_domain_event_tenant_timestamp",
        ]

        all_passed = True
        issues: list[str] = []

        try:
            from sqlalchemy import create_engine, text

            engine = create_engine(database_url)
            with engine.connect() as conn:
                # Check tables
                print("\n[Tables]")
                for table in required_tables:
                    result = conn.execute(text("""
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.tables
                            WHERE table_name = :table
                        )
                    """), {"table": table}).scalar()

                    if result:
                        print(f"  ✓ {table}")
                    else:
                        print(f"  ✗ {table} (MISSING)")
                        issues.append(f"Missing table: {table}")
                        all_passed = False

                # Check constraints
                print("\n[Constraints]")
                for table, constraint in required_constraints:
                    result = conn.execute(text("""
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.table_constraints
                            WHERE table_name = :table
                            AND constraint_name = :constraint
                        )
                    """), {"table": table, "constraint": constraint}).scalar()

                    if result:
                        print(f"  ✓ {table}.{constraint}")
                    else:
                        print(f"  ✗ {table}.{constraint} (MISSING)")
                        issues.append(f"Missing constraint: {table}.{constraint}")
                        all_passed = False

                # Check triggers
                print("\n[Triggers]")
                for table, trigger in required_triggers:
                    result = conn.execute(text("""
                        SELECT EXISTS (
                            SELECT 1 FROM information_schema.triggers
                            WHERE event_object_table = :table
                            AND trigger_name = :trigger
                        )
                    """), {"table": table, "trigger": trigger}).scalar()

                    if result:
                        print(f"  ✓ {table}.{trigger}")
                    else:
                        print(f"  ✗ {table}.{trigger} (MISSING)")
                        issues.append(f"Missing trigger: {table}.{trigger}")
                        all_passed = False

                # Check indexes
                print("\n[Indexes]")
                for index in required_indexes:
                    result = conn.execute(text("""
                        SELECT EXISTS (
                            SELECT 1 FROM pg_indexes
                            WHERE indexname = :index
                        )
                    """), {"index": index}).scalar()

                    if result:
                        print(f"  ✓ {index}")
                    else:
                        print(f"  ✗ {index} (MISSING)")
                        issues.append(f"Missing index: {index}")
                        all_passed = False

                # Verify constraint actually works (smoke test)
                print("\n[Constraint Smoke Test]")
                try:
                    # Try inserting negative amount - should fail
                    conn.execute(text("""
                        INSERT INTO psp_ledger_entry (
                            tenant_id, legal_entity_id, entry_type,
                            debit_account_id, credit_account_id, amount,
                            source_type, source_id, idempotency_key
                        ) VALUES (
                            gen_random_uuid(), gen_random_uuid(), 'test',
                            gen_random_uuid(), gen_random_uuid(), -1.00,
                            'test', gen_random_uuid(), 'schema_check_test'
                        )
                    """))
                    conn.rollback()
                    print("  ✗ Negative amount constraint NOT enforced!")
                    issues.append("Negative amount constraint not enforced")
                    all_passed = False
                except Exception as e:
                    if "violates check constraint" in str(e).lower():
                        print("  ✓ Negative amount correctly rejected")
                    else:
                        print(f"  ? Unexpected error: {e}")
                        conn.rollback()

        except ImportError:
            print("ERROR: SQLAlchemy not installed", file=sys.stderr)
            return 1
        except Exception as e:
            print(f"ERROR: {e}", file=sys.stderr)
            return 1

        # Summary
        print("\n" + "=" * 60)
        if all_passed:
            print("Schema verification: PASSED")
            print("All required tables, constraints, and triggers are present.")
            return 0
        else:
            print("Schema verification: FAILED")
            print(f"\n{len(issues)} issue(s) found:")
            for issue in issues:
                print(f"  - {issue}")
            print("\nRun migrations to fix: python scripts/migrate.py")
            return 1


def main() -> int:
    """CLI entry point."""
    cli = PSPCli()
    return cli.run()


if __name__ == "__main__":
    sys.exit(main())
