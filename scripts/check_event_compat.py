#!/usr/bin/env python
"""Event Schema Compatibility Checker.

This script enforces event versioning discipline:
1. Event names are immutable (no renames)
2. Payload fields are additive only (no removals)
3. Required fields cannot be added without defaults

Usage:
    python scripts/check_event_compat.py

Exit codes:
    0 - No breaking changes detected
    1 - Breaking changes detected (blocks CI)
"""

from __future__ import annotations

import json
import sys
from pathlib import Path


def load_baseline_schema(path: Path) -> dict:
    """Load the baseline event schema."""
    with open(path) as f:
        return json.load(f)


def get_current_events() -> dict:
    """
    Get current event definitions.

    In a real implementation, this would introspect the actual event classes.
    For now, we manually define what's current.
    """
    # This should be generated from actual code or maintained separately
    # For CI, we compare against event_schema.json
    return {
        "PaymentInstructionCreated": {
            "payload": {
                "required": ["instruction_id", "amount"],
                "optional": ["payee_name", "payee_type", "rail", "purpose"],
            }
        },
        "PaymentSubmitted": {
            "payload": {
                "required": ["instruction_id", "provider_ref"],
                "optional": ["provider_name", "rail"],
            }
        },
        "PaymentAccepted": {
            "payload": {
                "required": ["instruction_id", "provider_ref"],
                "optional": ["expected_settlement_date"],
            }
        },
        "PaymentSettled": {
            "payload": {
                "required": ["instruction_id"],
                "optional": ["settlement_id", "provider_ref", "settled_at", "trace_id"],
            }
        },
        "PaymentReturned": {
            "payload": {
                "required": ["instruction_id", "return_code"],
                "optional": ["return_reason", "return_date"],
            }
        },
        "PaymentFailed": {
            "payload": {
                "required": ["instruction_id", "error_code"],
                "optional": ["error_message", "retry_after"],
            }
        },
        "LedgerEntryPosted": {
            "payload": {
                "required": ["entry_id", "debit_account", "credit_account", "amount"],
                "optional": ["entry_type", "source_type", "source_id"],
            }
        },
        "LedgerEntryReversed": {
            "payload": {
                "required": ["entry_id", "reversal_entry_id"],
                "optional": ["reason"],
            }
        },
        "ReservationCreated": {
            "payload": {
                "required": ["reservation_id", "account_id", "amount"],
                "optional": ["purpose", "expires_at"],
            }
        },
        "ReservationReleased": {
            "payload": {
                "required": ["reservation_id"],
                "optional": ["release_type", "released_at"],
            }
        },
        "FundingRequested": {
            "payload": {
                "required": ["batch_id", "amount"],
                "optional": ["requested_at"],
            }
        },
        "FundingApproved": {
            "payload": {
                "required": ["batch_id"],
                "optional": ["reservation_id", "approved_at"],
            }
        },
        "FundingBlocked": {
            "payload": {
                "required": ["batch_id", "reason"],
                "optional": ["shortfall_amount", "policy_name"],
            }
        },
        "LiabilityClassified": {
            "payload": {
                "required": ["instruction_id", "return_code", "liability_party"],
                "optional": ["error_origin", "recovery_path", "amount"],
            }
        },
    }


def check_compatibility(baseline: dict, current: dict) -> list[str]:
    """
    Check for breaking changes between baseline and current schema.

    Returns list of breaking change descriptions.
    """
    breaking_changes: list[str] = []

    baseline_events = baseline.get("events", {})

    # Check for removed events
    for event_name in baseline_events:
        if event_name not in current:
            breaking_changes.append(
                f"REMOVED EVENT: '{event_name}' was removed. "
                f"Event names are immutable."
            )
            continue

        baseline_payload = baseline_events[event_name].get("payload", {})
        current_payload = current[event_name].get("payload", {})

        # Check for removed required fields
        baseline_required = set(baseline_payload.get("required", []))
        current_required = set(current_payload.get("required", []))

        removed_required = baseline_required - current_required
        for field in removed_required:
            breaking_changes.append(
                f"REMOVED REQUIRED FIELD: '{event_name}.{field}' was removed. "
                f"Payload fields are additive only."
            )

        # Check for removed optional fields
        baseline_optional = set(baseline_payload.get("optional", []))
        current_optional = set(current_payload.get("optional", []))

        removed_optional = baseline_optional - current_optional
        for field in removed_optional:
            breaking_changes.append(
                f"REMOVED OPTIONAL FIELD: '{event_name}.{field}' was removed. "
                f"Payload fields are additive only."
            )

        # Check for fields that moved from optional to required
        new_required = current_required - baseline_required
        previously_optional = new_required & baseline_optional

        for field in new_required - previously_optional:
            if field not in baseline_optional:
                breaking_changes.append(
                    f"NEW REQUIRED FIELD: '{event_name}.{field}' is new and required. "
                    f"New required fields must have defaults for backward compatibility."
                )

    return breaking_changes


def main() -> int:
    """Run the compatibility check."""
    print("Event Schema Compatibility Check")
    print("=" * 50)

    # Find the schema file
    script_dir = Path(__file__).parent
    repo_root = script_dir.parent
    schema_path = repo_root / "event_schema.json"

    if not schema_path.exists():
        print(f"ERROR: Baseline schema not found at {schema_path}")
        return 1

    # Load baseline
    print(f"\nLoading baseline from: {schema_path}")
    baseline = load_baseline_schema(schema_path)
    print(f"  Version: {baseline.get('version', 'unknown')}")
    print(f"  Events: {len(baseline.get('events', {}))}")

    # Get current schema
    print("\nLoading current event definitions...")
    current = get_current_events()
    print(f"  Events: {len(current)}")

    # Check compatibility
    print("\nChecking for breaking changes...")
    breaking_changes = check_compatibility(baseline, current)

    if breaking_changes:
        print("\n" + "=" * 50)
        print("BREAKING CHANGES DETECTED")
        print("=" * 50)

        for change in breaking_changes:
            print(f"\n  ❌ {change}")

        print("\n" + "-" * 50)
        print("These changes violate event versioning discipline.")
        print("Options:")
        print("  1. Revert the breaking change")
        print("  2. Create a V2 event (e.g., PaymentSettledV2)")
        print("  3. If intentional MAJOR version bump, update event_schema.json")

        return 1

    # Check for new events (informational)
    current_event_names = set(current.keys())
    baseline_event_names = set(baseline.get("events", {}).keys())
    new_events = current_event_names - baseline_event_names

    if new_events:
        print("\nNew events detected (non-breaking):")
        for event in new_events:
            print(f"  ✓ {event}")
        print("\nRemember to add these to event_schema.json for future baseline.")

    print("\n" + "=" * 50)
    print("✓ No breaking changes detected")
    print("=" * 50)

    return 0


if __name__ == "__main__":
    sys.exit(main())
