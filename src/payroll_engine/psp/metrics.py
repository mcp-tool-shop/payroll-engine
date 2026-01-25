"""PSP Observability Metrics.

Standardized metrics for monitoring PSP operations.

Metric Categories:
- Gate metrics: commit/pay gate evaluations
- Payment metrics: submission, settlement, return rates
- Reconciliation metrics: match rates, unmatched counts
- Ledger metrics: entry counts, balance summaries
- Event metrics: emission rates, subscription lag

Usage:
    collector = MetricsCollector(session)
    metrics = collector.collect_all()

    # For Prometheus export
    print(metrics.to_prometheus())

    # For JSON export
    print(metrics.to_json())
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.orm import Session


@dataclass
class Counter:
    """A counter metric (monotonically increasing)."""

    name: str
    value: int
    labels: dict[str, str] = field(default_factory=dict)
    help_text: str = ""


@dataclass
class Gauge:
    """A gauge metric (can go up or down)."""

    name: str
    value: float | int | Decimal
    labels: dict[str, str] = field(default_factory=dict)
    help_text: str = ""


@dataclass
class PSPMetrics:
    """Collection of all PSP metrics."""

    # Gate metrics
    commit_gate_total: Counter
    commit_gate_blocked: Counter
    pay_gate_total: Counter
    pay_gate_blocked: Counter

    # Payment metrics
    payments_created: Counter
    payments_submitted: Counter
    payments_settled: Counter
    payments_returned: Counter
    payments_failed: Counter
    payments_canceled: Counter

    # Payment breakdown by rail
    payments_by_rail: list[Counter]
    returns_by_code: list[Counter]

    # Reconciliation metrics
    reconciliation_runs: Counter
    reconciliation_matched: Counter
    reconciliation_unmatched: Gauge

    # Ledger metrics
    ledger_entries_total: Counter
    ledger_balance_total: Gauge

    # Event metrics
    domain_events_total: Counter
    event_subscriptions_active: Gauge
    event_subscription_lag_max: Gauge

    # Health indicators
    negative_balances: Gauge
    stuck_payments: Gauge
    pending_reversals: Gauge
    expired_reservations: Gauge

    # Timestamp
    collected_at: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict[str, Any]:
        """Convert to dictionary."""
        result: dict[str, Any] = {"collected_at": self.collected_at.isoformat()}

        for name, value in asdict(self).items():
            if name == "collected_at":
                continue
            if isinstance(value, list):
                result[name] = [self._metric_to_dict(m) for m in value]
            elif isinstance(value, (Counter, Gauge)):
                result[name] = self._metric_to_dict(value)
            else:
                result[name] = value

        return result

    def _metric_to_dict(self, metric: Counter | Gauge) -> dict[str, Any]:
        """Convert single metric to dict."""
        return {
            "name": metric.name,
            "value": float(metric.value) if isinstance(metric.value, Decimal) else metric.value,
            "labels": metric.labels,
            "help": metric.help_text,
        }

    def to_json(self) -> str:
        """Convert to JSON string."""
        return json.dumps(self.to_dict(), indent=2)

    def to_prometheus(self) -> str:
        """Convert to Prometheus text format."""
        lines = []

        def emit(metric: Counter | Gauge) -> None:
            labels = ""
            if metric.labels:
                label_parts = [f'{k}="{v}"' for k, v in metric.labels.items()]
                labels = "{" + ",".join(label_parts) + "}"

            value = float(metric.value) if isinstance(metric.value, Decimal) else metric.value

            if metric.help_text:
                lines.append(f"# HELP {metric.name} {metric.help_text}")

            metric_type = "counter" if isinstance(metric, Counter) else "gauge"
            lines.append(f"# TYPE {metric.name} {metric_type}")
            lines.append(f"{metric.name}{labels} {value}")

        # Emit all metrics
        for name, value in asdict(self).items():
            if name == "collected_at":
                continue
            if isinstance(value, list):
                for m in value:
                    if isinstance(m, dict):
                        # Reconstruct Counter from dict
                        emit(Counter(**m))
            elif isinstance(value, dict) and "name" in value:
                # Reconstruct from dict
                if "total" in value.get("name", "") or "created" in value.get("name", ""):
                    emit(Counter(**value))
                else:
                    emit(Gauge(**value))

        return "\n".join(lines)


class MetricsCollector:
    """Collects metrics from database."""

    def __init__(self, session: Session, tenant_id: UUID | None = None) -> None:
        self._session = session
        self._tenant_id = tenant_id

    def collect_all(self) -> PSPMetrics:
        """Collect all metrics."""
        return PSPMetrics(
            # Gate metrics
            commit_gate_total=self._count_gate_evaluations("commit", all=True),
            commit_gate_blocked=self._count_gate_evaluations("commit", blocked=True),
            pay_gate_total=self._count_gate_evaluations("pay", all=True),
            pay_gate_blocked=self._count_gate_evaluations("pay", blocked=True),
            # Payment metrics
            payments_created=self._count_payments(None),
            payments_submitted=self._count_payments("submitted"),
            payments_settled=self._count_payments("settled"),
            payments_returned=self._count_payments("returned"),
            payments_failed=self._count_payments("failed"),
            payments_canceled=self._count_payments("canceled"),
            # Payment breakdown
            payments_by_rail=self._count_payments_by_rail(),
            returns_by_code=self._count_returns_by_code(),
            # Reconciliation
            reconciliation_runs=self._count_reconciliation_runs(),
            reconciliation_matched=self._count_reconciliation_matched(),
            reconciliation_unmatched=self._gauge_unmatched_settlements(),
            # Ledger
            ledger_entries_total=self._count_ledger_entries(),
            ledger_balance_total=self._gauge_total_balance(),
            # Events
            domain_events_total=self._count_domain_events(),
            event_subscriptions_active=self._gauge_active_subscriptions(),
            event_subscription_lag_max=self._gauge_subscription_lag(),
            # Health
            negative_balances=self._gauge_negative_balances(),
            stuck_payments=self._gauge_stuck_payments(),
            pending_reversals=self._gauge_pending_reversals(),
            expired_reservations=self._gauge_expired_reservations(),
        )

    def _tenant_filter(self) -> str:
        """Get tenant filter clause."""
        if self._tenant_id:
            return f"AND tenant_id = '{self._tenant_id}'"
        return ""

    def _count_gate_evaluations(
        self,
        gate_type: str,
        all: bool = False,
        blocked: bool = False,
    ) -> Counter:
        """Count gate evaluations."""
        # Note: This assumes a gate_evaluation table exists
        # If not, return placeholder
        try:
            where = f"WHERE gate_type = :gate_type {self._tenant_filter()}"
            if blocked:
                where += " AND NOT passed"

            result = self._session.execute(
                text(f"SELECT COUNT(*) FROM psp_gate_evaluation {where}"),
                {"gate_type": gate_type},
            ).scalar()

            name = f"psp_{gate_type}_gate_{'blocked' if blocked else 'total'}"
            return Counter(
                name=name,
                value=result or 0,
                help_text=f"Total {gate_type} gate evaluations" + (" that were blocked" if blocked else ""),
            )
        except Exception:
            # Table might not exist
            name = f"psp_{gate_type}_gate_{'blocked' if blocked else 'total'}"
            return Counter(name=name, value=0, help_text="Gate evaluation count")

    def _count_payments(self, status: str | None) -> Counter:
        """Count payment instructions by status."""
        where = f"WHERE 1=1 {self._tenant_filter()}"
        if status:
            where += f" AND status = '{status}'"

        result = self._session.execute(
            text(f"SELECT COUNT(*) FROM payment_instruction {where}"),
        ).scalar()

        status_name = status or "created"
        return Counter(
            name=f"psp_payments_{status_name}_total",
            value=result or 0,
            help_text=f"Total payments {status_name}",
        )

    def _count_payments_by_rail(self) -> list[Counter]:
        """Count payments by rail."""
        try:
            results = self._session.execute(
                text(f"""
                    SELECT preferred_rail, status, COUNT(*)
                    FROM payment_instruction
                    WHERE 1=1 {self._tenant_filter()}
                    GROUP BY preferred_rail, status
                """),
            ).fetchall()

            counters = []
            for rail, status, count in results:
                counters.append(Counter(
                    name="psp_payments_by_rail_total",
                    value=count,
                    labels={"rail": rail or "unknown", "status": status},
                    help_text="Payments by rail and status",
                ))
            return counters
        except Exception:
            return []

    def _count_returns_by_code(self) -> list[Counter]:
        """Count returns by return code."""
        try:
            results = self._session.execute(
                text(f"""
                    SELECT return_code, COUNT(*)
                    FROM psp_settlement_event
                    WHERE return_code IS NOT NULL
                    {self._tenant_filter().replace('AND', 'AND' if 'WHERE' in self._tenant_filter() else '')}
                    GROUP BY return_code
                """),
            ).fetchall()

            counters = []
            for code, count in results:
                counters.append(Counter(
                    name="psp_returns_by_code_total",
                    value=count,
                    labels={"code": code},
                    help_text="Returns by return code",
                ))
            return counters
        except Exception:
            return []

    def _count_reconciliation_runs(self) -> Counter:
        """Count reconciliation runs."""
        # Placeholder - would query reconciliation job table
        return Counter(
            name="psp_reconciliation_runs_total",
            value=0,
            help_text="Total reconciliation job runs",
        )

    def _count_reconciliation_matched(self) -> Counter:
        """Count matched settlements."""
        try:
            result = self._session.execute(
                text(f"""
                    SELECT COUNT(*)
                    FROM psp_settlement_event
                    WHERE payment_instruction_id IS NOT NULL
                    {self._tenant_filter().replace('AND', 'AND' if 'WHERE' in self._tenant_filter() else '')}
                """),
            ).scalar()

            return Counter(
                name="psp_reconciliation_matched_total",
                value=result or 0,
                help_text="Settlements matched to instructions",
            )
        except Exception:
            return Counter(name="psp_reconciliation_matched_total", value=0)

    def _gauge_unmatched_settlements(self) -> Gauge:
        """Count unmatched settlements."""
        try:
            result = self._session.execute(
                text(f"""
                    SELECT COUNT(*)
                    FROM psp_settlement_event
                    WHERE payment_instruction_id IS NULL
                    {self._tenant_filter().replace('AND', 'AND' if 'WHERE' in self._tenant_filter() else '')}
                """),
            ).scalar()

            return Gauge(
                name="psp_reconciliation_unmatched",
                value=result or 0,
                help_text="Unmatched settlements (should be low)",
            )
        except Exception:
            return Gauge(name="psp_reconciliation_unmatched", value=0)

    def _count_ledger_entries(self) -> Counter:
        """Count total ledger entries."""
        try:
            result = self._session.execute(
                text(f"SELECT COUNT(*) FROM psp_ledger_entry WHERE 1=1 {self._tenant_filter()}"),
            ).scalar()

            return Counter(
                name="psp_ledger_entries_total",
                value=result or 0,
                help_text="Total ledger entries",
            )
        except Exception:
            return Counter(name="psp_ledger_entries_total", value=0)

    def _gauge_total_balance(self) -> Gauge:
        """Sum of all positive account balances."""
        try:
            result = self._session.execute(
                text(f"""
                    SELECT COALESCE(SUM(amount), 0)
                    FROM psp_ledger_entry
                    WHERE 1=1 {self._tenant_filter()}
                """),
            ).scalar()

            return Gauge(
                name="psp_ledger_balance_total",
                value=Decimal(str(result or 0)),
                help_text="Total ledger balance (all entries)",
            )
        except Exception:
            return Gauge(name="psp_ledger_balance_total", value=0)

    def _count_domain_events(self) -> Counter:
        """Count domain events."""
        try:
            result = self._session.execute(
                text(f"SELECT COUNT(*) FROM psp_domain_event WHERE 1=1 {self._tenant_filter()}"),
            ).scalar()

            return Counter(
                name="psp_domain_events_total",
                value=result or 0,
                help_text="Total domain events emitted",
            )
        except Exception:
            return Counter(name="psp_domain_events_total", value=0)

    def _gauge_active_subscriptions(self) -> Gauge:
        """Count active event subscriptions."""
        try:
            result = self._session.execute(
                text("SELECT COUNT(*) FROM psp_event_subscription WHERE is_active = true"),
            ).scalar()

            return Gauge(
                name="psp_event_subscriptions_active",
                value=result or 0,
                help_text="Active event subscriptions",
            )
        except Exception:
            return Gauge(name="psp_event_subscriptions_active", value=0)

    def _gauge_subscription_lag(self) -> Gauge:
        """Max subscription lag in seconds."""
        try:
            result = self._session.execute(
                text("""
                    SELECT MAX(EXTRACT(EPOCH FROM (NOW() - last_event_timestamp)))
                    FROM psp_event_subscription
                    WHERE is_active = true
                      AND last_event_timestamp IS NOT NULL
                """),
            ).scalar()

            return Gauge(
                name="psp_event_subscription_lag_seconds",
                value=float(result or 0),
                help_text="Maximum event subscription lag in seconds",
            )
        except Exception:
            return Gauge(name="psp_event_subscription_lag_seconds", value=0)

    def _gauge_negative_balances(self) -> Gauge:
        """Count accounts with negative balance (should be 0)."""
        try:
            result = self._session.execute(
                text(f"""
                    WITH balances AS (
                        SELECT
                            credit_account_id AS account_id,
                            SUM(amount) AS credits
                        FROM psp_ledger_entry
                        WHERE 1=1 {self._tenant_filter()}
                        GROUP BY credit_account_id
                    ),
                    debits AS (
                        SELECT
                            debit_account_id AS account_id,
                            SUM(amount) AS debits
                        FROM psp_ledger_entry
                        WHERE 1=1 {self._tenant_filter()}
                        GROUP BY debit_account_id
                    )
                    SELECT COUNT(*)
                    FROM (
                        SELECT COALESCE(b.credits, 0) - COALESCE(d.debits, 0) AS balance
                        FROM balances b
                        FULL OUTER JOIN debits d ON b.account_id = d.account_id
                    ) x
                    WHERE balance < 0
                """),
            ).scalar()

            return Gauge(
                name="psp_negative_balances",
                value=result or 0,
                help_text="Accounts with negative balance (ALERT if > 0)",
            )
        except Exception:
            return Gauge(name="psp_negative_balances", value=0)

    def _gauge_stuck_payments(self) -> Gauge:
        """Count payments stuck in non-terminal state for too long."""
        try:
            result = self._session.execute(
                text(f"""
                    SELECT COUNT(*)
                    FROM payment_instruction
                    WHERE status IN ('pending', 'submitted', 'accepted')
                      AND created_at < NOW() - INTERVAL '24 hours'
                    {self._tenant_filter()}
                """),
            ).scalar()

            return Gauge(
                name="psp_stuck_payments",
                value=result or 0,
                help_text="Payments stuck > 24h (investigate if > 0)",
            )
        except Exception:
            return Gauge(name="psp_stuck_payments", value=0)

    def _gauge_pending_reversals(self) -> Gauge:
        """Count liability events pending reversal."""
        try:
            result = self._session.execute(
                text(f"""
                    SELECT COUNT(*)
                    FROM liability_event
                    WHERE recovery_status = 'pending'
                    {self._tenant_filter()}
                """),
            ).scalar()

            return Gauge(
                name="psp_pending_reversals",
                value=result or 0,
                help_text="Liability events pending recovery",
            )
        except Exception:
            return Gauge(name="psp_pending_reversals", value=0)

    def _gauge_expired_reservations(self) -> Gauge:
        """Count expired but not released reservations."""
        try:
            result = self._session.execute(
                text(f"""
                    SELECT COUNT(*)
                    FROM psp_balance_reservation
                    WHERE status = 'active'
                      AND expires_at < NOW()
                    {self._tenant_filter()}
                """),
            ).scalar()

            return Gauge(
                name="psp_expired_reservations",
                value=result or 0,
                help_text="Reservations expired but not released",
            )
        except Exception:
            return Gauge(name="psp_expired_reservations", value=0)


@dataclass
class DailyHealthSummary:
    """Daily health summary for operators."""

    date: str
    unmatched_settlements: int
    stuck_payments_by_status: dict[str, int]
    negative_balance_count: int
    pending_reversals: int
    expired_reservations: int
    total_payments_24h: int
    return_rate_24h: float  # percentage
    alerts: list[str]


def generate_daily_health_summary(session: Session, tenant_id: UUID | None = None) -> DailyHealthSummary:
    """Generate daily health summary."""
    collector = MetricsCollector(session, tenant_id)
    metrics = collector.collect_all()

    alerts = []

    # Check for alert conditions
    if metrics.negative_balances.value > 0:
        alerts.append(f"CRITICAL: {metrics.negative_balances.value} accounts have negative balance")

    if metrics.reconciliation_unmatched.value > 10:
        alerts.append(f"WARNING: {metrics.reconciliation_unmatched.value} unmatched settlements")

    if metrics.stuck_payments.value > 0:
        alerts.append(f"WARNING: {metrics.stuck_payments.value} payments stuck > 24h")

    if metrics.expired_reservations.value > 0:
        alerts.append(f"INFO: {metrics.expired_reservations.value} expired reservations need cleanup")

    # Calculate return rate
    total = metrics.payments_created.value
    returned = metrics.payments_returned.value
    return_rate = (returned / total * 100) if total > 0 else 0.0

    if return_rate > 5.0:
        alerts.append(f"WARNING: Return rate is {return_rate:.1f}% (threshold: 5%)")

    return DailyHealthSummary(
        date=datetime.utcnow().date().isoformat(),
        unmatched_settlements=int(metrics.reconciliation_unmatched.value),
        stuck_payments_by_status={"total": int(metrics.stuck_payments.value)},
        negative_balance_count=int(metrics.negative_balances.value),
        pending_reversals=int(metrics.pending_reversals.value),
        expired_reservations=int(metrics.expired_reservations.value),
        total_payments_24h=int(metrics.payments_created.value),
        return_rate_24h=return_rate,
        alerts=alerts,
    )
