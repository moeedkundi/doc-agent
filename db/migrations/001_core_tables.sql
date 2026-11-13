-- 001: core tables (SPEC §5.2).
-- Every data table carries session_id so RLS (002) can isolate anonymous sessions.

CREATE TABLE sessions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    created_at  timestamptz NOT NULL DEFAULT now(),
    ip_hash     text,
    expires_at  timestamptz NOT NULL DEFAULT now() + interval '24 hours'
);

CREATE TABLE documents (
    id            uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id    uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    filename      text NOT NULL,
    doc_type      text CHECK (doc_type IN ('invoice', 'purchase_order', 'unknown')),
    pages         integer CHECK (pages > 0),
    storage_path  text NOT NULL,
    created_at    timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX documents_session_idx ON documents (session_id);

CREATE TABLE invoices (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    document_id     uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    invoice_number  text,
    invoice_date    date,
    due_date        date,
    currency        char(3),
    po_number       text,
    seller_name     text,
    seller_address  text,
    seller_tax_id   text,
    buyer_name      text,
    buyer_address   text,
    subtotal        numeric(14, 2),
    tax_amount      numeric(14, 2),
    total           numeric(14, 2),
    created_at      timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX invoices_session_idx ON invoices (session_id);
CREATE INDEX invoices_po_number_idx ON invoices (session_id, po_number);

CREATE TABLE invoice_lines (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    invoice_id   uuid NOT NULL REFERENCES invoices (id) ON DELETE CASCADE,
    line_no      integer NOT NULL CHECK (line_no >= 1),
    sku          text,
    description  text,
    quantity     numeric(12, 3),
    unit_price   numeric(14, 2),
    line_total   numeric(14, 2),
    UNIQUE (invoice_id, line_no)
);
CREATE INDEX invoice_lines_session_idx ON invoice_lines (session_id);

CREATE TABLE purchase_orders (
    id                       uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id               uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    document_id              uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    po_number                text,
    po_date                  date,
    currency                 char(3),
    payment_terms            text,
    buyer_name               text,
    buyer_address            text,
    vendor_name              text,
    vendor_address           text,
    vendor_tax_id            text,
    ship_to_address          text,
    requested_delivery_date  date,
    subtotal                 numeric(14, 2),
    tax_amount               numeric(14, 2),
    total                    numeric(14, 2),
    created_at               timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX purchase_orders_session_idx ON purchase_orders (session_id);
CREATE INDEX purchase_orders_po_number_idx ON purchase_orders (session_id, po_number);

CREATE TABLE po_lines (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    po_id        uuid NOT NULL REFERENCES purchase_orders (id) ON DELETE CASCADE,
    line_no      integer NOT NULL CHECK (line_no >= 1),
    sku          text,
    description  text,
    quantity     numeric(12, 3),
    unit_price   numeric(14, 2),
    line_total   numeric(14, 2),
    UNIQUE (po_id, line_no)
);
CREATE INDEX po_lines_session_idx ON po_lines (session_id);

CREATE TABLE invoice_po_matches (
    id           uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id   uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    invoice_id   uuid NOT NULL REFERENCES invoices (id) ON DELETE CASCADE,
    po_id        uuid REFERENCES purchase_orders (id) ON DELETE CASCADE,  -- NULL when po_not_found
    status       text NOT NULL CHECK (status IN ('matched', 'partial', 'mismatch', 'po_not_found')),
    issues_json  jsonb NOT NULL DEFAULT '[]'::jsonb,
    created_at   timestamptz NOT NULL DEFAULT now()
);
CREATE INDEX invoice_po_matches_session_idx ON invoice_po_matches (session_id);

CREATE TABLE runs (
    id              uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id      uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    document_id     uuid NOT NULL REFERENCES documents (id) ON DELETE CASCADE,
    status          text NOT NULL DEFAULT 'queued'
                    CHECK (status IN ('queued', 'running', 'awaiting_review',
                                      'completed', 'failed', 'rejected')),
    started_at      timestamptz NOT NULL DEFAULT now(),
    finished_at     timestamptz,
    total_cost_usd  numeric(12, 6) NOT NULL DEFAULT 0,
    escalated       boolean NOT NULL DEFAULT false
);
CREATE INDEX runs_session_idx ON runs (session_id);

CREATE TABLE trace_events (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    session_id   uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    run_id       uuid NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    seq          integer NOT NULL,
    ts           timestamptz NOT NULL DEFAULT now(),
    node         text NOT NULL,
    kind         text NOT NULL,
    tool         text,
    model        text,
    input_json   jsonb,
    output_json  jsonb,
    tokens_in    integer,
    tokens_out   integer,
    latency_ms   integer,
    cost_usd     numeric(12, 6),
    error        text,
    UNIQUE (run_id, seq)
);

CREATE TABLE review_actions (
    id          uuid PRIMARY KEY DEFAULT gen_random_uuid(),
    session_id  uuid NOT NULL REFERENCES sessions (id) ON DELETE CASCADE,
    run_id      uuid NOT NULL REFERENCES runs (id) ON DELETE CASCADE,
    field_path  text NOT NULL,
    old_value   jsonb,
    new_value   jsonb,
    action      text NOT NULL CHECK (action IN ('approve', 'edit')),
    created_at  timestamptz NOT NULL DEFAULT now()
);

-- Cost ledger. session_id is nullable so offline eval runs can be costed too; budget
-- checks need cross-session totals, so this table is NOT under RLS (see 002).
CREATE TABLE llm_ledger (
    id          bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    ts          timestamptz NOT NULL DEFAULT now(),
    session_id  uuid REFERENCES sessions (id) ON DELETE SET NULL,
    run_id      uuid REFERENCES runs (id) ON DELETE SET NULL,
    model       text NOT NULL,
    tokens_in   integer NOT NULL,
    tokens_out  integer NOT NULL,
    cost_usd    numeric(12, 6) NOT NULL
);
CREATE INDEX llm_ledger_ts_idx ON llm_ledger (ts);
CREATE INDEX llm_ledger_session_idx ON llm_ledger (session_id);
