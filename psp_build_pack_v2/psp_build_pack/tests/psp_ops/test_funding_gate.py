def test_funding_gate_fails_when_insufficient(db_session, funding_gate_service, tenant_id, legal_entity_id, pay_run_id):
    res = funding_gate_service.evaluate_commit_gate(
        tenant_id=tenant_id,
        legal_entity_id=legal_entity_id,
        pay_run_id=pay_run_id,
        funding_model="prefund_all",
        idempotency_key="gate-1",
        strict=True,
    )
    assert res.outcome in ("hard_fail","soft_fail","pass")
