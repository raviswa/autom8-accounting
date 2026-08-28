# autom8-accounting

Source-agnostic **transaction ledger** + (later) report engine + outbound accounting sinks.

This is **not** a double-entry accounting system. Zoho Books / Tally own posting, GST filing, and GL.

## This phase (D1 + D2)

- Canonical `fin_*` schema (Postgres, own instance)
- Autom8 webhook ingest → `fin_transactions` / lines / parties / items
- Munafe backend emits events via `emitLedgerEvent` (parallel to existing `pushPaidSaleToZoho`)

Deferred: Zoho sink, Reports tab, Shopify/Woo/Tally adapters.

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · Postgres

## Setup

```bash
python -m venv .venv
# Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env   # set DATABASE_URL + INGEST_WEBHOOK_SECRET
alembic upgrade head
uvicorn app.main:app --reload --port 8090
```

## Ingest

`POST /ingest/autom8` with header `X-Autom8-Signature: sha256=<hmac_hex>`  
HMAC-SHA256 of raw body using `INGEST_WEBHOOK_SECRET`.

Munafe env (backend Railway): `LEDGER_INGEST_URL`, `LEDGER_INGEST_SECRET` (same secret).

## Validation rules (this phase)

1. UNIQUE `(tenant_id, source_system, source_ref)` — no duplicate ingestion  
3. Header amount == Σ(line_amount + line_tax) within ₹0.02  
4. Concurrent webhooks safe via DB unique + transactional insert  

## Remote

https://github.com/raviswa/autom8-accounting
