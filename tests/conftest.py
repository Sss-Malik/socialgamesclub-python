import os

# settings.py reads these at import time — and calls .strip('"') on DB_PASS —
# so they must exist before anything imports settings, directly or transitively.
os.environ.setdefault("DB_USER", "test")
os.environ.setdefault("DB_PASS", "test")
os.environ.setdefault("DB_HOST", "localhost")
os.environ.setdefault("DB_NAME", "test")
os.environ.setdefault("APP_KEY", "test-app-key")
os.environ.setdefault("ACTIVATE_EMAILS", "False")

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from models import Base, WalletMaster, Coupon


@pytest.fixture
def session_factory(monkeypatch):
    """
    Point db_actions at a throwaway SQLite database.

    Only wallet_master and coupons are created: the rest of models.py leans on
    MySQL-only types (WalletDetail's LONGTEXT) that will not compile on SQLite.
    StaticPool keeps every session on one connection so an in-memory database
    survives across sessions.
    """
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(
        engine, tables=[WalletMaster.__table__, Coupon.__table__]
    )
    factory = sessionmaker(bind=engine)

    import common.utils.db_actions as db_actions

    monkeypatch.setattr(db_actions, "SessionLocal", factory)
    return factory


@pytest.fixture
def wallet(session_factory):
    """
    Build a wallet and return its id.

    The id is explicit because SQLite only autoincrements an INTEGER PRIMARY
    KEY, and wallet_master.id is a BigInteger.
    """

    def _make(balance=100, wallet_id=1):
        db = session_factory()
        try:
            db.add(
                WalletMaster(
                    id=wallet_id,
                    user_id=1,
                    balance_minor=balance,
                    currency="USD",
                )
            )
            db.commit()
            return wallet_id
        finally:
            db.close()

    return _make


def balance_of(session_factory, wallet_id):
    db = session_factory()
    try:
        return db.query(WalletMaster).filter_by(id=wallet_id).first().balance_minor
    finally:
        db.close()
