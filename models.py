# casino_automation/models.py
from sqlalchemy import Column, Integer, String, Text, Boolean, Enum, ForeignKey, DateTime, func, BigInteger, DECIMAL
from sqlalchemy.orm import relationship
from db import Base

class BackendGame(Base):
    __tablename__ = "backend_games"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False)
    backend_url = Column(String(255))
    username = Column(String(255))
    password = Column(String(255))
    game_url = Column(String(255))
    image_url = Column(String(255))
    accounts_creation_pd = Column(String(255))
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime)

    accounts = relationship("BackendAccount", back_populates="backend")


class BackendAccount(Base):
    __tablename__ = "backend_accounts"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String(255), nullable=False)
    password = Column(String(255), nullable=False)
    is_assigned = Column(Boolean, default=False)
    backend_id = Column(Integer, ForeignKey("backend_games.id"))
    game_id = Column(Integer, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime)

    backend = relationship("BackendGame", back_populates="accounts")
    user = relationship("User", back_populates="backend_accounts")

class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(Enum("error", "info", "warning", "debug"), nullable=False)
    description = Column(Text)
    source_url = Column(String(255))
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

class User(Base):
    __tablename__ = "users"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    first_name = Column(String(255), nullable=False)
    last_name = Column(String(255), nullable=False)
    phone = Column(String(255), nullable=False)
    user_name = Column(String(255), nullable=True)
    phone_number = Column(String(255), nullable=True)
    email = Column(String(255), nullable=False)
    referral_id = Column(String(255), nullable=True)
    referred_by = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    email_verified_at = Column(DateTime, nullable=True)
    password = Column(String(255), nullable=False)
    profile_pic = Column(String(255), nullable=True)
    is_active = Column(Boolean, nullable=False, default=True)
    is_ban = Column(Boolean, nullable=False, default=False)
    last_login_at = Column(DateTime, nullable=True)
    last_login_ip = Column(String(45), nullable=True)
    deleted_at = Column(DateTime, nullable=True)
    remember_token = Column(String(100), nullable=True)
    created_at = Column(DateTime, default=func.now(), nullable=True)
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now(), nullable=True)
    is_verified = Column(Boolean, nullable=False, default=False)
    freeplay_received = Column(Boolean, nullable=False, default=False)
    freeplay_transferred = Column(Boolean, nullable=False, default=False)
    freeplay_amount = Column(Integer, nullable=True)
    bonus_received = Column(Boolean, nullable=False, default=False)
    bonus_transferred = Column(Boolean, nullable=False, default=False)
    bonus_percentage = Column(Integer, nullable=True)

    # Optional: self-referencing relationship for referred_by
    referrer = relationship("User", remote_side=[id], backref="referrals", uselist=False)

    # Relationship
    backend_accounts = relationship("BackendAccount", back_populates="user")
    deposits = relationship("Deposit", back_populates="user")
    automation_results = relationship("AutomationResult", back_populates="user")

class Deposit(Base):
    __tablename__ = "deposits"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(String(255), ForeignKey("users.id"), nullable=False)
    payment_id = Column(String(255), nullable=True)
    order_id = Column(String(255), nullable=True)
    original_price = Column(DECIMAL(10, 2), nullable=True)
    description = Column(String(255), nullable=True)
    pay_price = Column(DECIMAL(10, 2), nullable=True)
    actually_paid = Column(DECIMAL(10, 2), nullable=True)
    outcome_price = Column(DECIMAL(10, 2), nullable=True)
    pay_currency = Column(String(10), nullable=True)
    type = Column(String(255), nullable=True)
    status = Column(String(255), nullable=False, default="pending")
    automation_status = Column(String(255), nullable=True, default="pending")
    payin_address = Column(String(255), nullable=True)
    payin_hash = Column(String(255), nullable=True)
    payout_hash = Column(String(255), nullable=True)
    network_fee = Column(DECIMAL(10, 2), nullable=True)
    service_fee = Column(DECIMAL(10, 2), nullable=True)
    invoice_url = Column(String(255), nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    user = relationship("User", back_populates="deposits")


class AutomationResult(Base):
    __tablename__ = "automation_results"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=True)
    description = Column(String(255), nullable=True)
    task_id = Column(String(36), nullable=True)
    status = Column(String(255), nullable=True, default="pending")
    data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    # Optional: relationship to User
    user = relationship("User", back_populates="automation_results")