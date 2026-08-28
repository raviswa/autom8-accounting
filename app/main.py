from __future__ import annotations

import hashlib
import hmac
import json
import os
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from sqlalchemy.orm import Session

from app.ingest import ReconcileError, ingest_raw_autom8
from db.session import get_engine, get_session

app = FastAPI(title="autom8-accounting", version="0.1.0")


def _verify_hmac(body: bytes, signature: str | None, secret: str) -> bool:
    if not signature:
        return False
    sig = signature.strip()
    if sig.lower().startswith("sha256="):
        sig = sig.split("=", 1)[1].strip()
    expected = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, sig)


@app.on_event("startup")
def _startup() -> None:
    get_engine()


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok", "service": "autom8-accounting"}


@app.post("/ingest/autom8")
async def ingest_autom8(
    request: Request,
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

    return {
        "ok": True,
        "created": result.created,
        "duplicate": result.duplicate,
        "transaction_id": str(result.transaction_id) if result.transaction_id else None,
    }
