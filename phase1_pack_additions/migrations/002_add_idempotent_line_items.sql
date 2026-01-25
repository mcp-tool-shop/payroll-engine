-- 002_add_idempotent_line_items.sql
ALTER TABLE pay_statement
  ADD COLUMN IF NOT EXISTS calculation_id UUID;

ALTER TABLE pay_line_item
  ADD COLUMN IF NOT EXISTS calculation_id UUID;

ALTER TABLE pay_line_item
  ADD COLUMN IF NOT EXISTS line_hash TEXT;

-- Backfill strategy left to implementation; for Phase 1, enforce NOT NULL for new rows at app layer.
-- When ready, uncomment:
-- ALTER TABLE pay_statement ALTER COLUMN calculation_id SET NOT NULL;
-- ALTER TABLE pay_line_item ALTER COLUMN calculation_id SET NOT NULL;
-- ALTER TABLE pay_line_item ALTER COLUMN line_hash SET NOT NULL;

CREATE UNIQUE INDEX IF NOT EXISTS pli_line_hash_unique
  ON pay_line_item(pay_statement_id, calculation_id, line_hash)
  WHERE calculation_id IS NOT NULL AND line_hash IS NOT NULL;
