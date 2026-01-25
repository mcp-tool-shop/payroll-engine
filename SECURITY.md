# Security Policy

## Supported Versions

| Version | Supported          |
|---------|--------------------|
| 0.1.x   | :white_check_mark: |

## Reporting a Vulnerability

**Do not report security vulnerabilities through public GitHub issues.**

Instead, please report them via email to:

**security@payroll-engine.example.com**

Include:
- Description of the vulnerability
- Steps to reproduce
- Potential impact
- Suggested fix (if any)

### What to Expect

1. **Acknowledgment**: Within 48 hours of your report
2. **Initial Assessment**: Within 5 business days
3. **Resolution Timeline**: Depends on severity
   - Critical: 7 days
   - High: 14 days
   - Medium: 30 days
   - Low: 60 days

### Disclosure Policy

- We follow coordinated disclosure
- We will credit you (unless you prefer anonymity)
- We may request additional information
- We will notify you when the fix is released

## Security Considerations

### What We Protect

PSP is a **library** that handles financial data. Security concerns include:

1. **Data Integrity**: Ledger entries are append-only, amounts are always positive
2. **Tenant Isolation**: Each tenant's data is isolated at the DB level
3. **Idempotency**: Operations are safe to retry without side effects
4. **Audit Trail**: All operations are logged as immutable events

### What We Don't Handle

PSP does **not** handle:
- Authentication (your app does this)
- Network security (your infrastructure)
- Encryption at rest (your DB config)
- Key management (your secrets store)

See [docs/threat_model.md](docs/threat_model.md) for the complete threat model.

### Security Best Practices

When using PSP:

1. **Never disable the pay gate** - `pay_gate_enabled=True` always
2. **Use idempotency keys** - Prevent duplicate payments
3. **Verify webhook signatures** - See provider documentation
4. **Isolate tenants** - Don't share sessions between tenants
5. **Audit regularly** - Use `psp export-events` for audit exports

### Known Limitations

- PSP trusts the DB connection - if your DB is compromised, PSP is compromised
- Event replay assumes event store integrity
- Provider credentials are passed via config - secure them appropriately

## Security Updates

Security fixes are released as patch versions (e.g., 0.1.1).

Subscribe to releases on GitHub to be notified of security updates.
