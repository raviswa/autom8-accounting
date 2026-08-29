from __future__ import annotations

import hashlib
import hmac
import json
import os
import uuid
from typing import Any, Optional

from fastapi import BackgroundTasks, Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.ingest import ReconcileError, ingest_raw_autom8
from db.models import FinSinkConfig
from db.session import get_engine, get_session
from reports.handlers import HANDLERS, aggregate_preview
from reports.registry import FREE_SLUGS, get_report, list_reports
from sinks.dispatch import push_transaction_to_sinks

app = FastAPI(title="autom8-accounting", version="0.2.0")


def _verify_hmac(body: bytes, signature: str | None, secret: str) -> bool:
    if not signature:
        return False
    sig = signature.strip()
    if sig.lower().startswith("sha256="):
        sig = sig.split("=", 1)[1].strip()
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


def _require_service_secret(
    x_ledger_secret: str | None = Header(default=None, alias="X-Ledger-Secret"),
) -> None:
    """Internal Munafe→accounting calls (reports, sink config)."""
    expected = os.environ.get("INGEST_WEBHOOK_SECRET", "").strip()
    allow = os.environ.get("ALLOW_INSECURE_INGEST", "").lower() in ("1", "true", "yes")
    if not expected:
        if allow:
            return
        raise HTTPException(503, "INGEST_WEBHOOK_SECRET not configured")
    if not x_ledger_secret or not hmac.compare_digest(x_ledger_secret, expected):
        raise HTTPException(401, "invalid service secret")


@app.on_event("startup")
def _startup() -> None:
    get_engine()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "autom8-accounting", "version": "0.2.0"}


def _run_sinks(transaction_id: str) -> None:
    from db.session import SessionLocal, get_engine

    get_engine()
    assert SessionLocal is not None
    db = SessionLocal()
    try:
        push_transaction_to_sinks(db, uuid.UUID(transaction_id))
    except Exception:  # noqa: BLE001
        pass
    finally:
        db.close()


@app.post("/ingest/autom8")
async def ingest_autom8(
    request: Request,
    background: BackgroundTasks,
    x_autom8_signature: str | None = Header(default=None, alias="X-Autom8-Signature"),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    body = await request.body()
    secret = os.environ.get("INGEST_WEBHOOK_SECRET", "").strip()
    allow_insecure = os.environ.get("ALLOW_INSECURE_INGEST", "").lower() in (
        "1",
        "true",
        "yes",
    )
    if secret:
        if not _verify_hmac(body, x_autom8_signature, secret):
            raise HTTPException(status_code=401, detail="invalid signature")
    elif not allow_insecure:
        raise HTTPException(
            status_code=503,
            detail="INGEST_WEBHOOK_SECRET not configured",
        )

    try:
        payload = json.loads(body.decode("utf-8") or "null")
    except Exception:
        raise HTTPException(status_code=400, detail="invalid JSON") from None

    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="payload must be an object")

    try:
        result = ingest_raw_autom8(db, payload)
    except ReconcileError as e:
        raise HTTPException(status_code=422, detail=str(e)) from e
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    if result.created and result.transaction_id:
        background.add_task(_run_sinks, str(result.transaction_id))

    return {
        "ok": True,
        "created": result.created,
        "duplicate": result.duplicate,
        "transaction_id": str(result.transaction_id) if result.transaction_id else None,
    }


# ----- Sink config -----


class SinkUpsertBody(BaseModel):
    tenant_id: uuid.UUID
    system: str
    is_active: bool = True
    config: dict[str, Any] = Field(default_factory=dict)
    credentials: dict[str, Any] = Field(default_factory=dict)


@app.get("/sinks")
def get_sinks(
    tenant_id: uuid.UUID = Query(...),
    _: None = Depends(_require_service_secret),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    rows = db.query(FinSinkConfig).filter_by(tenant_id=tenant_id).all()
    return {
        "sinks": [
            {
                "system": r.system,
                "is_active": r.is_active,
                "config": r.config,
                # never echo secrets
                "has_credentials": bool(r.credentials),
            }
            for r in rows
        ]
    }


@app.put("/sinks/{system}")
def upsert_sink(
    system: str,
    body: SinkUpsertBody,
    _: None = Depends(_require_service_secret),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    if system not in ("zoho_books", "tally"):
        raise HTTPException(400, "system must be zoho_books or tally")
    if body.system and body.system != system:
        raise HTTPException(400, "system mismatch")
    if system == "tally":
        mode = str((body.config or {}).get("delivery_mode") or "").lower()
        if mode and mode not in ("xml_http", "file_export"):
            raise HTTPException(400, "tally delivery_mode must be xml_http or file_export")

    row = (
        db.query(FinSinkConfig)
        .filter_by(tenant_id=body.tenant_id, system=system)
        .one_or_none()
    )
    if not row:
        row = FinSinkConfig(
            id=uuid.uuid4(),
            tenant_id=body.tenant_id,
            system=system,
            is_active=body.is_active,
            config=body.config or {},
            credentials=body.credentials or {},
        )
        db.add(row)
    else:
        row.is_active = body.is_active
        row.config = {**(row.config or {}), **(body.config or {})}
        if body.credentials:
            row.credentials = {**(row.credentials or {}), **body.credentials}
    db.commit()
    return {"ok": True, "system": system, "is_active": row.is_active, "config": row.config}


@app.post("/sinks/{system}/push/{transaction_id}")
def push_one(
    system: str,
    transaction_id: uuid.UUID,
    tenant_id: uuid.UUID = Query(...),
    _: None = Depends(_require_service_secret),
    db: Session = Depends(get_session),
) -> dict[str, Any]:
    results = push_transaction_to_sinks(db, transaction_id)
    filtered = [r for r in results if r.get("system") == system] or results
    return {"ok": True, "results": filtered}


# ----- Reports -----


@app.get("/reports")
def reports_catalog(
    _: None = Depends(_require_service_secret),
) -> dict[str, Any]:
    return {"reports": list_reports()}


@app.get("/reports/{report_type}")
def run_report(
    report_type: str,
    tenant_id: uuid.UUID = Query(...),
    tier: str = Query("free", description="free|paid — server-enforced"),
    source: Optional[str] = Query(None),
    date_from: Optional[str] = Query(None),
    date_to: Optional[str] = Query(None),
    _: None = Depends(_require_service_secret),
    db: Session = Depends(get_session),
):
    meta = get_report(report_type)
    if not meta:
        raise HTTPException(404, f"unknown report_type: {report_type}")

    tier_norm = (tier or "free").strip().lower()
    if tier_norm not in ("free", "paid"):
        tier_norm = "free"

    if meta.requires_gl and meta.tier == "paid":
        # Local GL not in scope — return structured stub for paid tenants
        if tier_norm != "paid":
            return JSONResponse(
                status_code=423,
                content={
                    "locked": True,
                    "report": report_type,
                    "tier": "paid",
                    "requires_gl": True,
                    "preview": {"message": "Upgrade to unlock. GL reports come from Zoho/Tally."},
                },
            )
        return {
            "report": report_type,
            "requires_gl": True,
            "data": None,
            "message": "Requires Zoho/Tally passthrough — not computed locally.",
        }

    handler = HANDLERS.get(report_type)
    if not handler:
        # Paid stub without handler
        if meta.tier == "paid" and tier_norm != "paid":
            return JSONResponse(
                status_code=423,
                content={
                    "locked": True,
                    "report": report_type,
                    "tier": "paid",
                    "preview": {"message": "Upgrade to unlock this report."},
                },
            )
        return {
            "report": report_type,
            "data": None,
            "message": "Handler not implemented yet — registered as paid placeholder.",
        }

    full = handler(
        db,
        tenant_id,
        date_from=date_from,
        date_to=date_to,
        source=source,
    )

    if meta.tier == "paid" and tier_norm != "paid":
        return JSONResponse(
            status_code=423,
            content={
                "locked": True,
                "report": report_type,
                "tier": "paid",
                "preview": aggregate_preview(full),
            },
        )

    return {"ok": True, "tier": meta.tier, "data": full}
