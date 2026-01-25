-- 003_payment_batch_idempotency.sql
ALTER TABLE payment_batch
  ADD CONSTRAINT payment_batch_one_per_run UNIQUE (pay_run_id, processor);
