# Support

## Getting Help

### Before Asking

1. **Read the docs**: Check [docs/](docs/) for guides and references
2. **Search issues**: Your question may already be answered
3. **Check non-goals**: Review [docs/non_goals.md](docs/non_goals.md) - we won't build everything

### Where to Ask

| Question Type | Where |
|---------------|-------|
| General questions | [GitHub Discussions](https://github.com/payroll-engine/payroll-engine/discussions) |
| Bug reports | [GitHub Issues](https://github.com/payroll-engine/payroll-engine/issues/new?template=bug_report.yml) |
| Feature requests | [GitHub Issues](https://github.com/payroll-engine/payroll-engine/issues/new?template=feature_request.yml) |
| Security issues | security@payroll-engine.com (not public issues) |

### What to Include

When reporting issues, include:

- **Version**: `pip show payroll-engine`
- **Python version**: `python --version`
- **OS**: Windows/Linux/macOS
- **Minimal reproduction**: Smallest code that shows the problem
- **Expected vs actual**: What you expected and what happened
- **Full error**: Complete stack trace, not just the last line

## What We Support

### Supported

- Core PSP functionality (ledger, funding gates, payments)
- Public API documented in [docs/public_api.md](docs/public_api.md)
- PostgreSQL 15+
- Python 3.11+

### Not Supported

- Internal/private APIs (subject to change without notice)
- Features listed in [docs/non_goals.md](docs/non_goals.md)
- End-user payroll questions (this is infrastructure, not a payroll product)
- Custom integrations beyond documented patterns

## Response Times

This is an open source project. Maintainers respond as time allows.

- **Security issues**: Best effort within 48 hours
- **Bug reports**: Reviewed weekly
- **Feature requests**: Discussed when bandwidth allows
- **Questions**: Community members may respond faster than maintainers

## Commercial Support

No commercial support is currently offered.

If you need guaranteed SLAs, consider:
- Building internal expertise
- Engaging a consulting firm
- Sponsoring development of specific features
