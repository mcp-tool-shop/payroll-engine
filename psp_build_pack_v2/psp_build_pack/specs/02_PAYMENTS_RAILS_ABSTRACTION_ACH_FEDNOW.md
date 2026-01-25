# 02 — Payment Rails Abstractions (ACH / Wire / RTP / FedNow) — Bank-Agnostic

**Goal:** Provide a stable interface so the PSP can support multiple banks/processors (including Amegy / Zions) without rewriting
payroll, ledger, or payment orchestration logic.

## Key Separation
- **Payment Instruction:** what we *intend* to do (business intent, idempotent).
- **Payment Attempt:** how we tried to do it (rail/provider specific).
- **Settlement Event:** what actually happened (accepted/settled/failed).

## Core Entities
### payment_instruction
- `payment_instruction_id` (uuid)
- `tenant_id`, `legal_entity_id`
- `purpose` (text): `employee_net`, `tax_remit`, `third_party`, `refund`, `fee`
- `direction` (outbound/inbound)
- `amount`, `currency`
- `payee_type`: `employee`, `agency`, `provider`, `client`
- `payee_ref_id` (employee_id / tax_agency_id / third_party_profile_id)
- `requested_settlement_date` (date)
- `status`: `created`, `queued`, `submitted`, `accepted`, `settled`, `failed`, `reversed`, `canceled`
- `idempotency_key` UNIQUE(tenant_id, idempotency_key)
- `source_type`, `source_id` (pay_statement/tax_liability/etc.)
- `created_at`

### payment_attempt
- `payment_attempt_id`
- `payment_instruction_id`
- `rail` (ach, wire, rtp, fednow, check)
- `provider` (text) — e.g., bank name, processor name
- `provider_request_id` (text)
- `status` (submitted/accepted/failed)
- `request_payload_json` (redacted/tokenized)
- `created_at`

### settlement_event
(Use `psp_settlement_event` in ledger spec; reference it here.)

## Bank-Agnostic Provider Interface
Implement a provider adapter per bank/processor:

```python
class PaymentRailProvider(Protocol):
    def capabilities(self) -> RailCapabilities: ...
    def submit(self, instruction: PaymentInstruction) -> SubmitResult: ...
    def get_status(self, provider_request_id: str) -> StatusResult: ...
    def cancel(self, provider_request_id: str) -> CancelResult: ...  # if supported
    def reconcile(self, date: datetime.date) -> list[SettlementEvent]: ...
```

### RailCapabilities
- supported rails
- cut-off times
- max amount per transaction
- return code mappings
- settlement timelines (same-day vs next-day vs instant)

## ACH (NACHA) specifics
**Outbound (credit):**
- file/batch creation (if bank requires files) OR API instruction (if bank offers)
- trace numbers and effective dates
- return codes (R01, R02, R03, R29, etc.)
- prenotes (optional)
- micro-deposit verification (for client funding accounts)

**Inbound (debit) for funding:**
- authorization evidence (client KYB/KYC)
- SEC codes (CCD/PPD/etc.)
- returns/NSF handling
- retries policy and holdbacks

## FedNow specifics
FedNow is message-based, near-real-time settlement.
Requirements:
- idempotent message submission
- immediate acceptance/settlement responses
- handling of rejects and timeouts
- limit controls and fraud checks
- reconciliation via message IDs

## RTP specifics (The Clearing House)
Similar abstractions to FedNow, but different message set and rails.

## Wire specifics
- request/approval workflow
- IMAD/OMAD identifiers
- recall limitations
- OFAC screening requirements

## Canonical Workflow (Orchestrator)
1) Build `payment_instruction` (idempotent)
2) Choose rail based on:
   - payee preference
   - amount limits
   - urgency
   - risk score
3) Create `payment_attempt` and submit to provider
4) Create `psp_settlement_event` when accepted/settled
5) Link settlement event to ledger entry
6) Update instruction status

## Reconciliation Loop (Daily)
- Pull provider settlement results
- Match to instructions via trace/message IDs
- Post settlement ledger entries if not already posted (idempotent)
- Flag unmatched items (bank shows movement with no instruction)

## Risk Hooks (Do not skip)
- pre-submit risk scoring
- cooling-off for bank changes
- step-up auth for high-risk actions
- velocity limits by client and admin
