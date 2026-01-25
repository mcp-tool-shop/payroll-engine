#!/bin/bash
# Demo Reset Script
# Drops and reseeds the demo database
#
# Usage:
#   ./demo/scripts/reset.sh
#   DEMO_DATABASE_URL=postgresql://... ./demo/scripts/reset.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# Default database URL
DEMO_DATABASE_URL="${DEMO_DATABASE_URL:-postgresql://demo_writer:demo_writer_secret@localhost:5432/payroll_demo}"

echo "=============================================="
echo "Payroll Engine Demo Reset"
echo "=============================================="
echo ""
echo "Database: ${DEMO_DATABASE_URL%%@*}@..."
echo ""

# Run seeder with drop-first flag
cd "$PROJECT_ROOT"
python demo/scripts/seed.py --database-url "$DEMO_DATABASE_URL" --drop-first

echo ""
echo "=============================================="
echo "Demo reset complete!"
echo "=============================================="
echo ""
echo "Start the API:"
echo "  DEMO_DATABASE_URL=postgresql://demo_reader:demo_reader_secret@localhost:5432/payroll_demo \\"
echo "  uvicorn demo.api.main:app --reload --port 8000"
echo ""
echo "Open the UI:"
echo "  http://localhost:8000"
echo ""
