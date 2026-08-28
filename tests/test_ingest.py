from __future__ import annotations

import os
import uuid
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

# Force SQLite before importing engine helpers
os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
os.environ["ALLOW_INSECURE_INGEST"] = "true"

from app.ingest import ReconcileError, ingest_normalized, ingest_raw_autom8  # noqa: E402
from db.models import Base, FinTransaction  # noqa: E402
from db.reconcile import reconcile_header_vs_lines  # noqa: E402
from db.session import get_engine, reset_engine  # noqa: E402
from sources.autom8 import translate  # noqa: E402
from sources.base import NormalizedLine, NormalizedParty, NormalizedTransaction  # noqa: E402

TENANT = uuid.UUID("aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee")


@pytest.fixture()
def db() -> Session:
    reset_engine()
    os.environ["DATABASE_URL"] = "sqlite+pysqlite:///:memory:"
    engine = get_engine()
    Base.metadata.create_all(bind=engine)
    from db.session import SessionLocal

    assert SessionLocal is not None
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()
        Base.metadata.drop_all(bind=engine)
        reset_engine()


def _sale_payload(**overrides):
    base = {
        "event": "order.paid",
        "tenant_id": str(TENANT),
        "source_ref": "sale:test-1",
        "date": "2026-08-28",
        "lob": "restaurant",
        "service_type": "dine_in",
        "payment_mode": "upi",
        "amount": "118.00",
        "tax_amount": "18.00",
        "tax_breakdown": {"cgst": 9, "sgst": 9},
        "party": {"source_ref": "91xxxxxxxxxx", "name": "Test Guest"},
        "lines": [
            {
                "item_source_ref": "item-1",
                "item_name": "Masala Dosa",
                "qty": 2,
                "rate": 50,
                "line_amount": "100.00",
                "line_tax": "18.00",
                "hsn_sac": "996331",
                "gst_rate": 18,
            }
        ],
    }
    base.update(overrides)
    return base


def test_translate_order_paid_shape():
    nt = translate(_sale_payload())
    assert nt.source_system == "autom8"
    assert nt.txn_type == "sale"
    assert nt.category == "restaurant/dine_in"
    assert len(nt.lines) == 1
    assert nt.amount == Decimal("118.00")
    rec = reconcile_header_vs_lines(
        nt.amount, nt.tax_amount, [l.line_amount for l in nt.lines], [l.line_tax for l in nt.lines]
    )
    assert rec.ok


def test_translate_refund_and_stock():
    refund = translate(
        {
            "event": "refund.issued",
            "tenant_id": str(TENANT),
            "source_ref": "refund:booking-1",
            "amount": "118.00",
            "tax_amount": "18.00",
            "party": {"source_ref": "91xxxxxxxxxx", "name": "Test Guest"},
            "lines": [
                {
                    "item_source_ref": "item-1",
                    "item_name": "Masala Dosa",
                    "qty": 2,
                    "rate": 50,
                    "line_amount": "100.00",
                    "line_tax": "18.00",
                }
            ],
        }
    )
    assert refund.txn_type == "refund"

    stock = translate(
        {
            "event": "stock.adjustment",
            "tenant_id": str(TENANT),
            "source_ref": "stock:deduct:item-1:1",
            "amount": "0",
            "tax_amount": "0",
            "lines": [
                {
                    "item_source_ref": "item-1",
                    "item_name": "Masala Dosa",
                    "qty": -2,
                    "rate": 0,
                    "line_amount": "0",
                    "line_tax": "0",
                }
            ],
        }
    )
    assert stock.txn_type == "stock_adjustment"
    rec = reconcile_header_vs_lines(
        stock.amount,
        stock.tax_amount,
        [l.line_amount for l in stock.lines],
        [l.line_tax for l in stock.lines],
    )
    assert rec.ok


def test_ingest_rejects_bad_reconcile(db: Session):
    with pytest.raises(ReconcileError):
        ingest_raw_autom8(
            db,
            _sale_payload(amount="999.00", tax_amount="18.00"),
        )


def test_duplicate_ingestion(db: Session):
    r1 = ingest_raw_autom8(db, _sale_payload())
    r2 = ingest_raw_autom8(db, _sale_payload())
    assert r1.created and not r1.duplicate
    assert r2.duplicate and not r2.created
    assert r1.transaction_id == r2.transaction_id
    count = db.query(FinTransaction).filter_by(tenant_id=TENANT).count()
    assert count == 1


def test_concurrent_ingestion():
    """Simultaneous inserts for same source_ref → exactly one row (rule 4)."""
    import tempfile
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import NullPool

    reset_engine()
    fd, path = tempfile.mkstemp(suffix=".db")
    os.close(fd)
    try:
        engine = create_engine(
            f"sqlite+pysqlite:///{path}",
            connect_args={"check_same_thread": False, "timeout": 30},
            poolclass=NullPool,
        )

        @event.listens_for(engine, "connect")
        def _set_sqlite_pragma(dbapi_conn, _connection_record):
            cursor = dbapi_conn.cursor()
            cursor.execute("PRAGMA busy_timeout=30000")
            cursor.close()

        Base.metadata.create_all(bind=engine)
        SessionFactory = sessionmaker(bind=engine, autoflush=False, autocommit=False)
        payload = _sale_payload(source_ref="sale:concurrent-1")

        def worker(_i):
            s = SessionFactory()
            try:
                return ingest_raw_autom8(s, payload)
            finally:
                s.close()

        with ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(worker, range(4)))

        created = sum(1 for r in results if r.created)
        duplicates = sum(1 for r in results if r.duplicate)
        assert created == 1, results
        assert duplicates == 3, results
        s = SessionFactory()
        try:
            assert (
                s.query(FinTransaction).filter_by(source_ref="sale:concurrent-1").count()
                == 1
            )
        finally:
            s.close()
        Base.metadata.drop_all(bind=engine)
        engine.dispose()
    finally:
        try:
            os.remove(path)
        except OSError:
            pass
        reset_engine()


def test_order_created_mapping():
    nt = translate(
        {
            "event": "order.created",
            "tenant_id": str(TENANT),
            "source_ref": "order:abc",
            "amount": "50.00",
            "tax_amount": "0",
            "lines": [
                {
                    "item_source_ref": "i1",
                    "item_name": "Tea",
                    "qty": 1,
                    "rate": 50,
                    "line_amount": "50.00",
                    "line_tax": "0",
                }
            ],
            "party": {"source_ref": "walkin", "name": "Walk-in"},
        }
    )
    assert nt.txn_type == "sale"
    assert nt.source_ref == "order:abc"
