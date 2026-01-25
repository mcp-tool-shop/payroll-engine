from decimal import Decimal

def test_ledger_idempotency(db_session, ledger_service, tenant_id, legal_entity_id, acct_a, acct_b):
    eid1 = ledger_service.post_entry(
        tenant_id=tenant_id,
        legal_entity_id=legal_entity_id,
        idempotency_key="post-1",
        entry_type="funding_received",
        debit_account_id=acct_a,
        credit_account_id=acct_b,
        amount=Decimal("100.00"),
        source_type="funding_request",
        source_id="00000000-0000-0000-0000-000000000000",
        metadata={"note":"test"},
    )
    eid2 = ledger_service.post_entry(
        tenant_id=tenant_id,
        legal_entity_id=legal_entity_id,
        idempotency_key="post-1",
        entry_type="funding_received",
        debit_account_id=acct_a,
        credit_account_id=acct_b,
        amount=Decimal("100.00"),
        source_type="funding_request",
        source_id="00000000-0000-0000-0000-000000000000",
        metadata={"note":"test"},
    )
    assert eid1 == eid2
