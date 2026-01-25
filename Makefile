# Payroll Engine - PSP Development Makefile
# ==========================================
#
# Quick Start:
#   make up       - Start PostgreSQL
#   make migrate  - Apply database migrations
#   make demo     - Run the PSP minimal example
#
# Full reset:
#   make reset    - Stop, delete data, restart, migrate

.PHONY: help up down migrate demo test lint typecheck ci reset clean logs shell health

# Default target
help:
	@echo "Payroll Engine - PSP Development"
	@echo "================================="
	@echo ""
	@echo "Quick Start:"
	@echo "  make up        Start PostgreSQL container"
	@echo "  make migrate   Apply database migrations"
	@echo "  make demo      Run the PSP minimal example"
	@echo ""
	@echo "Development:"
	@echo "  make test      Run all tests"
	@echo "  make lint      Run linter (ruff)"
	@echo "  make typecheck Run type checker (pyright)"
	@echo "  make ci        Run all CI checks"
	@echo ""
	@echo "Database:"
	@echo "  make logs      Show PostgreSQL logs"
	@echo "  make shell     Open psql shell"
	@echo "  make reset     Full reset (delete data + restart)"
	@echo "  make health    Check database health"
	@echo ""
	@echo "Cleanup:"
	@echo "  make down      Stop containers"
	@echo "  make clean     Remove all containers and volumes"

# =============================================================================
# Docker / Database
# =============================================================================

DATABASE_URL ?= postgresql://payroll:payroll_dev@localhost:5432/payroll_dev

up:
	@echo "Starting PostgreSQL..."
	docker compose up -d postgres
	@echo "Waiting for database to be ready..."
	@sleep 2
	@docker compose exec postgres pg_isready -U payroll -d payroll_dev || (sleep 3 && docker compose exec postgres pg_isready -U payroll -d payroll_dev)
	@echo "PostgreSQL is ready!"

down:
	@echo "Stopping containers..."
	docker compose down

logs:
	docker compose logs -f postgres

shell:
	docker compose exec postgres psql -U payroll -d payroll_dev

health:
	@echo "Database Health Check"
	@echo "====================="
	@docker compose exec postgres psql -U payroll -d payroll_dev -c "\
		SELECT 'Connection' as check, 'OK' as status; \
		SELECT 'Tables' as check, COUNT(*)::text as status FROM information_schema.tables WHERE table_schema = 'public'; \
		SELECT 'Ledger Entries' as check, COALESCE(COUNT(*)::text, '0') as status FROM psp_ledger_entry; \
		SELECT 'Payment Instructions' as check, COALESCE(COUNT(*)::text, '0') as status FROM payment_instruction; \
		SELECT 'Domain Events' as check, COALESCE(COUNT(*)::text, '0') as status FROM psp_domain_event; \
	" 2>/dev/null || echo "Database not ready or tables not created yet"

reset: clean up
	@sleep 2
	$(MAKE) migrate
	@echo "Reset complete!"

clean:
	@echo "Removing containers and volumes..."
	docker compose down -v --remove-orphans
	@echo "Clean complete!"

# =============================================================================
# Migrations
# =============================================================================

migrate:
	@echo "Applying migrations..."
	python scripts/migrate.py --database-url "$(DATABASE_URL)"

migrate-dry:
	@echo "Dry run migrations..."
	python scripts/migrate.py --database-url "$(DATABASE_URL)" --dry-run

# =============================================================================
# Demo
# =============================================================================

demo:
	@echo "Running PSP Minimal Example..."
	@echo ""
	python examples/psp_minimal/main.py --database-url "$(DATABASE_URL)"

demo-dry:
	@echo "Dry run PSP Minimal Example..."
	python examples/psp_minimal/main.py --dry-run

# =============================================================================
# Testing
# =============================================================================

test:
	pytest tests/ -v

test-psp:
	pytest tests/psp/ -v

test-fast:
	pytest tests/ -v -x --tb=short

test-cov:
	pytest tests/ -v --cov=src/payroll_engine --cov-report=html --cov-report=term

# =============================================================================
# Linting / Type Checking
# =============================================================================

lint:
	ruff check src/ tests/

lint-fix:
	ruff check src/ tests/ --fix

typecheck:
	pyright src/

format:
	ruff format src/ tests/

# =============================================================================
# CI
# =============================================================================

ci: lint typecheck test
	@echo ""
	@echo "All CI checks passed!"

ci-db: up migrate
	@echo "Running database constraint tests..."
	pytest tests/psp/test_red_team_scenarios.py -v
	@echo ""
	@echo "Database constraint tests passed!"

# =============================================================================
# CLI Tools
# =============================================================================

psp-health:
	python -m payroll_engine.psp.cli health

psp-metrics:
	python -m payroll_engine.psp.cli metrics --format json

psp-balance:
	@echo "Usage: make psp-balance TENANT=<uuid> ACCOUNT=<uuid>"
	python -m payroll_engine.psp.cli balance --tenant-id $(TENANT) --account-id $(ACCOUNT)

psp-events:
	python -m payroll_engine.psp.cli subscriptions --list

# =============================================================================
# Development Helpers
# =============================================================================

install:
	pip install -e ".[dev]"

install-deps:
	pip install sqlalchemy psycopg2-binary pytest pytest-asyncio ruff pyright

# Show what would be run
.PHONY: print-%
print-%:
	@echo $* = $($*)
