-- 005_immutability_triggers.sql
-- Prevent UPDATE/DELETE of pay_statement and pay_line_item once parent pay_run is committed/paid.
-- NOTE: Keep trigger functions in public schema for simplicity; adjust as needed.

CREATE OR REPLACE FUNCTION prevent_mutation_on_committed_payroll()
RETURNS trigger AS $$
DECLARE
  v_status TEXT;
BEGIN
  SELECT pr.status INTO v_status
  FROM pay_run pr
  JOIN pay_run_employee pre ON pre.pay_run_id = pr.pay_run_id
  JOIN pay_statement ps ON ps.pay_run_employee_id = pre.pay_run_employee_id
  WHERE ps.pay_statement_id = COALESCE(NEW.pay_statement_id, OLD.pay_statement_id);

  IF v_status IN ('committed','paid') THEN
    RAISE EXCEPTION 'Cannot modify payroll artifacts when pay_run status is %', v_status;
  END IF;

  RETURN COALESCE(NEW, OLD);
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS trg_no_update_pay_statement ON pay_statement;
CREATE TRIGGER trg_no_update_pay_statement
BEFORE UPDATE OR DELETE ON pay_statement
FOR EACH ROW EXECUTE FUNCTION prevent_mutation_on_committed_payroll();

DROP TRIGGER IF EXISTS trg_no_update_pay_line_item ON pay_line_item;
CREATE TRIGGER trg_no_update_pay_line_item
BEFORE UPDATE OR DELETE ON pay_line_item
FOR EACH ROW EXECUTE FUNCTION prevent_mutation_on_committed_payroll();
