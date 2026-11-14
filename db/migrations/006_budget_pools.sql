-- 006: separate spend pools so offline evals don't use up the public demo's daily budget.
-- A session is 'demo' (visitors) or 'eval' (evals/runner.py); every ledger row records the
-- pool it was charged to. The gateway caps 'demo' with DAILY_BUDGET_USD (+ per session) and
-- 'eval' with EVAL_BUDGET_USD.

ALTER TABLE sessions ADD COLUMN purpose text NOT NULL DEFAULT 'demo'
    CHECK (purpose IN ('demo', 'eval'));
ALTER TABLE llm_ledger ADD COLUMN pool text NOT NULL DEFAULT 'demo'
    CHECK (pool IN ('demo', 'eval'));
CREATE INDEX llm_ledger_pool_ts_idx ON llm_ledger (pool, ts);
