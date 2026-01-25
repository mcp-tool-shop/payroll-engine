# Runbook: AI Advisory System

This runbook covers operational procedures for the PSP AI Advisory system.

## Critical Constraint

**AI is advisory-only. It NEVER:**
- Moves money
- Writes ledger entries
- Overrides funding gates
- Makes final liability decisions

All advisories require human or policy confirmation before action.

---

## Understanding Advisories

### Return Advisory

When a payment returns, the AI generates a `ReturnAdvisoryGenerated` event with:

| Field | Description |
|-------|-------------|
| `suggested_error_origin` | Who likely caused the error (employee, employer, provider, psp) |
| `suggested_liability_party` | Who should bear the cost |
| `suggested_recovery_path` | How to recover (offset, clawback, write_off, investigate) |
| `confidence` | How certain the AI is (0.0 to 1.0) |
| `contributing_factors` | Why the AI made this decision |

### Funding Risk Advisory

Before payroll commit, the AI generates a `FundingRiskAdvisoryGenerated` event with:

| Field | Description |
|-------|-------------|
| `risk_score` | Probability of funding failure (0.0 to 1.0) |
| `risk_band` | Category: low, medium, high, critical |
| `suggested_reserve_buffer` | Recommended additional reserve |
| `contributing_factors` | What's driving the risk |

---

## Interpreting Confidence Scores

| Score | Meaning | Action |
|-------|---------|--------|
| 95%+ | Very high confidence | Generally safe to accept automatically via policy |
| 85-95% | High confidence | Review if amount is significant |
| 70-85% | Moderate confidence | Manual review recommended |
| 50-70% | Low confidence | Investigation required |
| <50% | Very low confidence | AI is uncertain, do not rely on suggestion |

---

## Common Scenarios

### Scenario: High-Confidence Return Attribution

**Symptoms:**
- Return code is R01 (insufficient funds)
- New employee account (<14 days old)
- AI confidence >90%
- Suggested origin: employee

**Response:**
1. Review the advisory in event store
2. If confidence is high and factors make sense:
   - Accept the liability classification
   - Initiate recovery via suggested path
3. If anything seems off:
   - Mark for manual investigation
   - Do not auto-classify

### Scenario: Low-Confidence Return

**Symptoms:**
- AI confidence <60%
- Contributing factors are mixed
- Suggested origin: unknown

**Response:**
1. This requires manual investigation
2. Review the payment history
3. Check the return code meaning
4. Contact the tenant if needed
5. Make a human decision
6. Record the actual classification (may differ from AI)

### Scenario: Critical Funding Risk Alert

**Symptoms:**
- Risk band: critical
- Risk score >70%
- Recent funding blocks
- Insufficient headroom

**Response:**
1. **Do not ignore** - critical risk means likely failure
2. Review contributing factors
3. Options:
   - Delay payroll until funding confirmed
   - Request additional funding from tenant
   - Switch to prefunded model temporarily
4. If proceeding anyway, increase reserve buffer as suggested
5. Monitor closely during execution

### Scenario: AI Advisory Disagrees with Human Judgment

**Symptoms:**
- AI suggests one attribution
- Ops team believes different attribution is correct

**Response:**
1. Human judgment overrides AI
2. Record the human decision with reason
3. This creates valuable training data for future model improvements
4. Example:
   ```sql
   INSERT INTO liability_classification (
     payment_id,
     ai_suggested_origin,
     actual_origin,
     override_reason,
     classified_by
   ) VALUES (
     '...',
     'employee',
     'employer',
     'Employer confirmed they provided wrong routing number',
     'ops_team'
   );
   ```

---

## Disabling AI Advisories

### Disable for a Specific Tenant

```python
# In tenant config
tenant_config.ai_advisory_enabled = False
```

Or via API/CLI:
```bash
psp ai-disable --tenant-id <TENANT_ID>
```

### Disable Globally

```python
# In PSP config
psp_config.ai = AdvisoryConfig(enabled=False)
```

Or set environment variable:
```bash
export PSP_AI_ENABLED=false
```

### Verify AI is Disabled

```bash
psp ai-health
# Should show: "AI Advisory: DISABLED"
```

---

## Auditing AI Decisions

### View Recent Advisories

```bash
psp export-events --event-type ReturnAdvisoryGenerated --since "24 hours ago"
psp export-events --event-type FundingRiskAdvisoryGenerated --since "24 hours ago"
```

### Compare AI vs Actual Decisions

```sql
SELECT
  ra.payment_id,
  ra.suggested_error_origin AS ai_suggestion,
  lc.error_origin AS actual_decision,
  ra.confidence,
  CASE WHEN ra.suggested_error_origin = lc.error_origin
       THEN 'match' ELSE 'override' END AS outcome
FROM return_advisory ra
LEFT JOIN liability_classification lc ON ra.payment_id = lc.payment_id
WHERE ra.created_at > NOW() - INTERVAL '30 days';
```

### Calculate AI Accuracy

```sql
SELECT
  COUNT(*) AS total,
  SUM(CASE WHEN ai_suggestion = actual THEN 1 ELSE 0 END) AS correct,
  ROUND(100.0 * SUM(CASE WHEN ai_suggestion = actual THEN 1 ELSE 0 END) / COUNT(*), 1) AS accuracy_pct
FROM (
  SELECT
    ra.suggested_error_origin AS ai_suggestion,
    lc.error_origin AS actual
  FROM return_advisory ra
  JOIN liability_classification lc ON ra.payment_id = lc.payment_id
  WHERE ra.created_at > NOW() - INTERVAL '30 days'
) x;
```

---

## Troubleshooting

### AI Not Generating Advisories

1. Check if AI is enabled:
   ```bash
   psp ai-health
   ```

2. Check if events are being emitted:
   ```bash
   psp export-events --event-type ReturnAdvisoryGenerated --limit 5
   ```

3. Check for errors in logs:
   ```bash
   grep "AI Advisory" /var/log/psp/psp.log | tail -20
   ```

4. Verify feature extraction is working:
   - Feature extraction requires historical events
   - New tenants may have insufficient history

### AI Giving Poor Recommendations

1. Check the contributing factors in the advisory
2. Look for data quality issues:
   - Missing historical events
   - Incorrect event timestamps
   - Corrupted payee data

3. Check if the model is appropriate:
   - Rules baseline may not fit unusual patterns
   - Consider model retraining if ML is enabled

4. Report the case for model improvement:
   - Save the advisory ID
   - Record what the correct decision was
   - Submit to model training pipeline

### High False Positive Rate on Funding Risk

1. Check if thresholds are appropriate for tenant size
2. Review the spike detection logic:
   - Small tenants have high variance
   - Consider tenant-specific thresholds

3. Adjust configuration:
   ```python
   ai_config = AdvisoryConfig(
     lookback_days=180,  # Longer history for better baseline
   )
   ```

---

## Model Information

### Current Model: rules_baseline v1.0.0

**Characteristics:**
- Deterministic rules based on return codes and patterns
- Zero external dependencies
- Instant explainability
- Safe fallback behavior

**Return Code Classifications:**
- R01-R04: Employee fault (account issues)
- R05-R09: Mixed fault (authorization)
- R10-R16: Employer/PSP fault (processing errors)
- R17+: Provider fault (bank issues)

**Funding Risk Factors:**
- Payroll spike (>1.5x average)
- Recent funding blocks
- Settlement delays
- Insufficient headroom

### Future Models

ML models may be added in future versions. They will:
- Require explicit opt-in
- Still be advisory-only
- Include the rules baseline as fallback
- Produce the same event format

---

## Escalation Path

| Severity | Condition | Action |
|----------|-----------|--------|
| P3 | AI accuracy <80% over 7 days | Review model, consider retraining |
| P2 | AI generating incorrect critical recommendations | Disable for affected tenants |
| P1 | AI recommendations causing operational issues | Disable globally, investigate |

---

## Related Runbooks

- [Returns](returns.md) - Manual return handling
- [Settlement Mismatch](settlement_mismatch.md) - Reconciliation issues
- [Funding Gate Blocks](funding_blocks.md) - When funding fails
