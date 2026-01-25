# PSP Integration Recipes

Copy-paste recipes for common PSP integration patterns.

## Available Recipes

| Recipe | Use Case |
|--------|----------|
| [Batch Payroll Run](./batch_payroll_run.md) | Embed PSP into payroll processing |
| [Ledger Only](./ledger_only.md) | Use ledger + reconciliation without funding gates |
| [Custom Provider](./custom_provider.md) | Implement your own payment provider |

## How to Use These

1. Find the recipe closest to your use case
2. Copy the code into your project
3. Adapt to your specific requirements
4. Run through PSP's test suite

## Prerequisites

All recipes assume you have:
- PSP installed (`pip install payroll-engine`)
- PostgreSQL with migrations applied (`python scripts/migrate.py`)
- Basic understanding of PSP concepts (see [README](../../README.md))

## Contributing Recipes

If you've built a useful PSP integration pattern:
1. Create a new file in this directory
2. Follow the existing recipe format
3. Include runnable code examples
4. Submit a PR

Good recipes are:
- **Complete** - Can be copied and run
- **Annotated** - Explain the "why" not just the "what"
- **Tested** - Include test examples
