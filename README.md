# autom8-accounting

Source-agnostic **transaction ledger** + report engine + outbound accounting sinks.

This is **not** a double-entry accounting system. Zoho Books / Tally own posting, GST filing, and GL.

## Phases shipped

- **D1–D2:** Canonical `fin_*` schema, Autom8 webhook ingest, line/header reconcile
- **P3:** `sinks/zoho_books` + `sinks/tally` (dual mode: `xml_http` | `file_export`)
- **P4:** Report registry (9 free + paid placeholders), `423 Locked` gating
- **P5:** Source stubs — shopify / woocommerce / csv_upload / generic_webhook

## Tally delivery modes

Owner picks in Munafe **Integrations → Tally**:

| Mode | Config | Behavior |
|------|--------|----------|
| `xml_http` | `gateway_url` | POST Tally XML to local/gateway HTTP server |
| `file_export` | `export_dir` (or `TALLY_EXPORT_DIR`) | Write `.xml` voucher files for import |

## Stack

Python 3.12 · FastAPI · SQLAlchemy 2 · Alembic · Postgres

## Setup

```bash
python -m venv .venv
pip install -r requirements.txt
cp .env.example .env
alembic upgrade head
uvicorn app.main:app --reload --port 8090
```

## Env

**autom8-accounting Railway:** `DATABASE_URL`, `INGEST_WEBHOOK_SECRET`, optional `TALLY_EXPORT_DIR`  
**autom8-backend / chat Railway:** `LEDGER_INGEST_URL`, `LEDGER_INGEST_SECRET` (same secret)

## Zoho cutover note

Keep Munafe `pushPaidSaleToZoho` in parallel until ledger-sink output is reconciled against it for the same sales.

## Remote

https://github.com/raviswa/autom8-accounting
