"""Sink idempotency + report gating tests."""

from __future__ import annotations

import os
import uuid
from datetime import date
from decimal import Decimal
from unittest.mock import MagicMock

import pytest

os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["ALLOW_INSECURE_INGEST"] = "true"
os.environ["INGEST_WEBHOOK_SECRET"] = "test-secret"

from db.models import (  # noqa: E402
    Base,
    FinParty,
    FinSinkConfig,
    FinSyncLog,
    FinTransaction,
    FinTransactionLine,
    TransactionType,
)
from db.session import get_engine, reset_engine  # noqa: E402
from reports.handlers import aggregate_preview, daily_sales_summary  # noqa: E402
from reports.registry import FREE_SLUGS, get_report  # noqa: E402
from sinks.sync_log import already_synced_successfully, append_outbound_log  # noqa: E402
from sinks.tally import TallySink, build_sales_voucher_xml  # noqa: E402
from sinks.zoho_books import ZohoBooksSink  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from app.main import app  # noqa: E402

TENANT = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


@pytest.fixture()
def db():
    reset_engine()
    os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    from db.session import SessionLocal

    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        reset_engine()


def _tx(db, source_ref="sale:1"):
    party = FinParty(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        source_system="autom8",
        source_ref="91xx",
        name="Guest",
        external_mappings={},
    )
    db.add(party)
    db.flush()
    tx = FinTransaction(
        id=uuid.uuid4(),
        tenant_id=TENANT,
        source_system="autom8",
        source_ref=source_ref,
        date=date.today(),
        type=TransactionType.sale,
        amount=Decimal("100.00"),
        tax_amount=Decimal("0"),
        tax_breakdown={},
        party_id=party.id,
    )
    db.add(tx)
    db.flush()
    db.add(
        FinTransactionLine(
            id=uuid.uuid4(),
            tenant_id=TENANT,
            transaction_id=tx.id,
            qty=Decimal("1"),
            rate=Decimal("100"),
            line_amount=Decimal("100.00"),
            line_tax=Decimal("0"),
        )
    )
    db.commit()
    db.refresh(tx)
    return tx, party


def test_zoho_idempotent_skip(db):
    tx, party = _tx(db)
    append_outbound_log(
        db,
        tenant_id=TENANT,
        transaction_id=tx.id,
        system="zoho_books",
        status="success",
    )
    db.commit()
    assert already_synced_successfully(db, transaction_id=tx.id, system="zoho_books")

    client = MagicMock()
    sink = ZohoBooksSink(http_client=client)
    res = sink.push(
        tenant_id=TENANT,
        transaction=tx,
        config={"organization_id": "1"},
        credentials={"access_token": "tok"},
        db=db,
        lines=list(tx.lines),
        party=party,
    )
    assert res.status == "skipped"
    client.post.assert_not_called()


def test_zoho_partial_failure_recovers_by_reference(db):
    tx, party = _tx(db, source_ref="sale:recover")
    # Phone too short → skip contact search GET; only invoice lookup GET
    party.source_ref = "walkin"
    db.commit()

    mock = MagicMock()

    contact_resp = MagicMock()
    contact_resp.status_code = 200
    contact_resp.content = b"{}"
    contact_resp.json.return_value = {"code": 0, "contact": {"contact_id": "C1"}}

    find_resp = MagicMock()
    find_resp.status_code = 200
    find_resp.content = b"{}"
    find_resp.json.return_value = {
        "invoices": [
            {
                "invoice_id": "INV-EXISTING",
                "reference_number": "autom8:sale:recover",
            }
        ]
    }

    mock.get.return_value = find_resp
    mock.post.return_value = contact_resp

    sink = ZohoBooksSink(http_client=mock)
    res = sink.push(
        tenant_id=TENANT,
        transaction=tx,
        config={"organization_id": "org", "api_domain": "https://www.zohoapis.in"},
        credentials={"access_token": "tok"},
        db=db,
        lines=list(tx.lines),
        party=party,
    )
    assert res.status == "success", res.error_detail
    assert res.external_id == "INV-EXISTING"
    assert res.meta.get("recovered") is True
    assert mock.post.call_count == 1  # contact only


def test_tally_xml_and_file_modes(db, tmp_path):
    tx, party = _tx(db, source_ref="sale:tally")
    xml = build_sales_voucher_xml(tx=tx, lines=list(tx.lines), party=party, config={})
    assert "VOUCHER" in xml and "Sales" in xml

    sink = TallySink()
    res = sink.push(
        tenant_id=TENANT,
        transaction=tx,
        config={"delivery_mode": "file_export", "export_dir": str(tmp_path)},
        credentials={},
        db=db,
        lines=list(tx.lines),
        party=party,
    )
    assert res.status == "success"
    assert res.external_id and os.path.exists(res.external_id)

    res2 = sink.push(
        tenant_id=TENANT,
        transaction=tx,
        config={"delivery_mode": "file_export", "export_dir": str(tmp_path)},
        credentials={},
        db=db,
        lines=list(tx.lines),
        party=party,
    )
    assert res2.status == "skipped"


def test_report_gating_423(db):
    from db.session import get_session as real_get_session
    from app.main import app as fastapi_app

    def _override():
        try:
            yield db
        finally:
            pass

    fastapi_app.dependency_overrides[real_get_session] = _override
    # get_session is imported into main — override the one main uses
    from app import main as main_mod

    fastapi_app.dependency_overrides[main_mod.get_session] = _override
    client = TestClient(fastapi_app)
    headers = {"X-Ledger-Secret": "test-secret"}

    r = client.get(
        f"/reports/top_skus?tenant_id={TENANT}&tier=free",
        headers=headers,
    )
    assert r.status_code == 423
    assert r.json().get("locked") is True

    r2 = client.get(
        f"/reports/daily_sales_summary?tenant_id={TENANT}&tier=free",
        headers=headers,
    )
    assert r2.status_code == 200
    assert r2.json()["data"]["report"] == "daily_sales_summary"

    r3 = client.get(
        f"/reports/top_skus?tenant_id={TENANT}&tier=paid",
        headers=headers,
    )
    assert r3.status_code == 200
    fastapi_app.dependency_overrides.clear()


def test_free_slugs_count():
    assert len(FREE_SLUGS) == 9
    assert get_report("daily_sales_summary").tier == "free"
    assert get_report("top_skus").tier == "paid"
