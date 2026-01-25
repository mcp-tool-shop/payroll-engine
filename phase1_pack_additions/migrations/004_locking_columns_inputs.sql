-- 004_locking_columns_inputs.sql
ALTER TABLE time_entry
  ADD COLUMN IF NOT EXISTS locked_by_pay_run_id UUID REFERENCES pay_run(pay_run_id),
  ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;

ALTER TABLE pay_input_adjustment
  ADD COLUMN IF NOT EXISTS locked_by_pay_run_id UUID REFERENCES pay_run(pay_run_id),
  ADD COLUMN IF NOT EXISTS locked_at TIMESTAMPTZ;
