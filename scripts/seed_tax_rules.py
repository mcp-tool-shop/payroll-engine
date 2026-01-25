"""Seed script for initial tax rules.

Run with:
    python scripts/seed_tax_rules.py

This creates the basic federal tax rules needed for payroll calculation.
"""

from __future__ import annotations

import asyncio
from datetime import date, datetime
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from payroll_engine.database import get_session
from payroll_engine.models import Jurisdiction, PayrollRule, PayrollRuleVersion, TaxAgency


async def seed_jurisdictions(session: AsyncSession) -> dict[str, Jurisdiction]:
    """Create federal and sample state jurisdictions."""
    jurisdictions = {}

    # Check if FED exists
    result = await session.execute(
        select(Jurisdiction).where(
            Jurisdiction.jurisdiction_type == "FED",
            Jurisdiction.code == "FED",
        )
    )
    fed = result.scalar_one_or_none()

    if fed is None:
        fed = Jurisdiction(
            jurisdiction_id=uuid4(),
            jurisdiction_type="FED",
            code="FED",
            name="Federal",
        )
        session.add(fed)
        print("Created Federal jurisdiction")
    jurisdictions["FED"] = fed

    # California
    result = await session.execute(
        select(Jurisdiction).where(
            Jurisdiction.jurisdiction_type == "STATE",
            Jurisdiction.code == "CA",
        )
    )
    ca = result.scalar_one_or_none()

    if ca is None:
        ca = Jurisdiction(
            jurisdiction_id=uuid4(),
            jurisdiction_type="STATE",
            code="CA",
            name="California",
            parent_jurisdiction_id=fed.jurisdiction_id,
        )
        session.add(ca)
        print("Created California jurisdiction")
    jurisdictions["CA"] = ca

    await session.flush()
    return jurisdictions


async def seed_federal_rules(session: AsyncSession) -> None:
    """Create federal tax rules for 2024."""

    # Check if rules already exist
    result = await session.execute(
        select(PayrollRule).where(PayrollRule.rule_name == "federal_income_tax")
    )
    if result.scalar_one_or_none():
        print("Federal rules already exist, skipping...")
        return

    now = datetime.utcnow()

    # Federal Income Tax (2024 brackets for single filer)
    fed_income_rule = PayrollRule(
        rule_id=uuid4(),
        rule_name="federal_income_tax",
        rule_type="tax",
    )
    fed_income_version = PayrollRuleVersion(
        rule_version_id=uuid4(),
        rule_id=fed_income_rule.rule_id,
        effective_start=date(2024, 1, 1),
        source_url="https://www.irs.gov/newsroom/irs-provides-tax-inflation-adjustments-for-tax-year-2024",
        source_last_verified_at=now,
        logic_hash="fed_income_2024_single",
        payload_json={
            "tax_type": "federal_income",
            "jurisdiction_type": "FED",
            "jurisdiction_code": "FED",
            "brackets": [
                {"min": 0, "max": 11600, "rate": 0.10, "flat": 0},
                {"min": 11600, "max": 47150, "rate": 0.12, "flat": 1160},
                {"min": 47150, "max": 100525, "rate": 0.22, "flat": 5426},
                {"min": 100525, "max": 191950, "rate": 0.24, "flat": 17168.5},
                {"min": 191950, "max": 243725, "rate": 0.32, "flat": 39110.5},
                {"min": 243725, "max": 609350, "rate": 0.35, "flat": 55678.5},
                {"min": 609350, "max": None, "rate": 0.37, "flat": 183647.25},
            ],
        },
    )
    session.add_all([fed_income_rule, fed_income_version])
    print("Created federal income tax rule")

    # Social Security (Employee)
    ss_ee_rule = PayrollRule(
        rule_id=uuid4(),
        rule_name="social_security_employee",
        rule_type="tax",
    )
    ss_ee_version = PayrollRuleVersion(
        rule_version_id=uuid4(),
        rule_id=ss_ee_rule.rule_id,
        effective_start=date(2024, 1, 1),
        source_url="https://www.ssa.gov/oact/cola/cbb.html",
        source_last_verified_at=now,
        logic_hash="ss_ee_2024",
        payload_json={
            "tax_type": "social_security",
            "jurisdiction_type": "FED",
            "jurisdiction_code": "FED",
            "brackets": [{"min": 0, "max": None, "rate": 0.062, "flat": 0}],
            "wage_base_limit": 168600,
            "is_employer_tax": False,
        },
    )
    session.add_all([ss_ee_rule, ss_ee_version])
    print("Created Social Security (employee) rule")

    # Social Security (Employer)
    ss_er_rule = PayrollRule(
        rule_id=uuid4(),
        rule_name="social_security_employer",
        rule_type="tax",
    )
    ss_er_version = PayrollRuleVersion(
        rule_version_id=uuid4(),
        rule_id=ss_er_rule.rule_id,
        effective_start=date(2024, 1, 1),
        source_url="https://www.ssa.gov/oact/cola/cbb.html",
        source_last_verified_at=now,
        logic_hash="ss_er_2024",
        payload_json={
            "tax_type": "social_security",
            "jurisdiction_type": "FED",
            "jurisdiction_code": "FED",
            "brackets": [{"min": 0, "max": None, "rate": 0.062, "flat": 0}],
            "wage_base_limit": 168600,
            "is_employer_tax": True,
        },
    )
    session.add_all([ss_er_rule, ss_er_version])
    print("Created Social Security (employer) rule")

    # Medicare (Employee)
    med_ee_rule = PayrollRule(
        rule_id=uuid4(),
        rule_name="medicare_employee",
        rule_type="tax",
    )
    med_ee_version = PayrollRuleVersion(
        rule_version_id=uuid4(),
        rule_id=med_ee_rule.rule_id,
        effective_start=date(2024, 1, 1),
        source_url="https://www.ssa.gov/oact/cola/cbb.html",
        source_last_verified_at=now,
        logic_hash="med_ee_2024",
        payload_json={
            "tax_type": "medicare",
            "jurisdiction_type": "FED",
            "jurisdiction_code": "FED",
            "brackets": [{"min": 0, "max": None, "rate": 0.0145, "flat": 0}],
            "is_employer_tax": False,
        },
    )
    session.add_all([med_ee_rule, med_ee_version])
    print("Created Medicare (employee) rule")

    # Medicare (Employer)
    med_er_rule = PayrollRule(
        rule_id=uuid4(),
        rule_name="medicare_employer",
        rule_type="tax",
    )
    med_er_version = PayrollRuleVersion(
        rule_version_id=uuid4(),
        rule_id=med_er_rule.rule_id,
        effective_start=date(2024, 1, 1),
        source_url="https://www.ssa.gov/oact/cola/cbb.html",
        source_last_verified_at=now,
        logic_hash="med_er_2024",
        payload_json={
            "tax_type": "medicare",
            "jurisdiction_type": "FED",
            "jurisdiction_code": "FED",
            "brackets": [{"min": 0, "max": None, "rate": 0.0145, "flat": 0}],
            "is_employer_tax": True,
        },
    )
    session.add_all([med_er_rule, med_er_version])
    print("Created Medicare (employer) rule")

    # FUTA
    futa_rule = PayrollRule(
        rule_id=uuid4(),
        rule_name="futa",
        rule_type="tax",
    )
    futa_version = PayrollRuleVersion(
        rule_version_id=uuid4(),
        rule_id=futa_rule.rule_id,
        effective_start=date(2024, 1, 1),
        source_url="https://www.irs.gov/taxtopics/tc759",
        source_last_verified_at=now,
        logic_hash="futa_2024",
        payload_json={
            "tax_type": "futa",
            "jurisdiction_type": "FED",
            "jurisdiction_code": "FED",
            "brackets": [{"min": 0, "max": None, "rate": 0.006, "flat": 0}],
            "wage_base_limit": 7000,
            "is_employer_tax": True,
        },
    )
    session.add_all([futa_rule, futa_version])
    print("Created FUTA rule")

    await session.flush()


async def seed_california_rules(session: AsyncSession) -> None:
    """Create California state tax rules."""

    # Check if rules already exist
    result = await session.execute(
        select(PayrollRule).where(PayrollRule.rule_name == "state_income_tax_ca")
    )
    if result.scalar_one_or_none():
        print("California rules already exist, skipping...")
        return

    now = datetime.utcnow()

    # CA State Income Tax (simplified 2024 brackets)
    ca_income_rule = PayrollRule(
        rule_id=uuid4(),
        rule_name="state_income_tax_ca",
        rule_type="tax",
    )
    ca_income_version = PayrollRuleVersion(
        rule_version_id=uuid4(),
        rule_id=ca_income_rule.rule_id,
        effective_start=date(2024, 1, 1),
        source_url="https://www.ftb.ca.gov/forms/2024-California-Tax-Rates-and-Exemptions.shtml",
        source_last_verified_at=now,
        logic_hash="ca_income_2024_single",
        payload_json={
            "tax_type": "state_income",
            "jurisdiction_type": "STATE",
            "jurisdiction_code": "CA",
            "brackets": [
                {"min": 0, "max": 10412, "rate": 0.01, "flat": 0},
                {"min": 10412, "max": 24684, "rate": 0.02, "flat": 104.12},
                {"min": 24684, "max": 38959, "rate": 0.04, "flat": 389.56},
                {"min": 38959, "max": 54081, "rate": 0.06, "flat": 960.56},
                {"min": 54081, "max": 68350, "rate": 0.08, "flat": 1867.88},
                {"min": 68350, "max": 349137, "rate": 0.093, "flat": 3009.40},
                {"min": 349137, "max": 418961, "rate": 0.103, "flat": 29122.59},
                {"min": 418961, "max": 698271, "rate": 0.113, "flat": 36314.46},
                {"min": 698271, "max": None, "rate": 0.123, "flat": 67876.49},
            ],
        },
    )
    session.add_all([ca_income_rule, ca_income_version])
    print("Created California income tax rule")

    # CA SDI (State Disability Insurance)
    ca_sdi_rule = PayrollRule(
        rule_id=uuid4(),
        rule_name="suta_ca",
        rule_type="tax",
    )
    ca_sdi_version = PayrollRuleVersion(
        rule_version_id=uuid4(),
        rule_id=ca_sdi_rule.rule_id,
        effective_start=date(2024, 1, 1),
        source_url="https://edd.ca.gov/en/Payroll_Taxes/Rates_and_Withholding/",
        source_last_verified_at=now,
        logic_hash="ca_sdi_2024",
        payload_json={
            "tax_type": "sdi",
            "jurisdiction_type": "STATE",
            "jurisdiction_code": "CA",
            "brackets": [{"min": 0, "max": None, "rate": 0.011, "flat": 0}],
            "wage_base_limit": 153164,
            "is_employer_tax": False,
        },
    )
    session.add_all([ca_sdi_rule, ca_sdi_version])
    print("Created California SDI rule")

    await session.flush()


async def main():
    """Run seed script."""
    print("Seeding tax rules...")

    async with get_session() as session:
        await seed_jurisdictions(session)
        await seed_federal_rules(session)
        await seed_california_rules(session)
        await session.commit()

    print("\nDone! Tax rules seeded successfully.")


if __name__ == "__main__":
    asyncio.run(main())
