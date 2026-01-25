-- 001_add_statement_uniqueness.sql
ALTER TABLE pay_statement
  ADD CONSTRAINT pay_statement_one_per_pre UNIQUE (pay_run_employee_id);
