# casino_automation/models.py
from sqlalchemy import Column, Integer, String, Text, Boolean, Enum, ForeignKey, DateTime, func, BigInteger, DECIMAL, TIMESTAMP, Float, CHAR, JSON
from sqlalchemy.orm import relationship
from db import Base
from datetime import datetime

class BackendGame(Base):
    __tablename__ = "backend_games"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(255), unique=True, nullable=False)
    backend_url = Column(String(255))
    username = Column(String(255))
    password = Column(String(255))
    game_url = Column(String(255))
    binding_key = Column(String(32), nullable=True)
    image_url = Column(String(255))
    accounts_creation_pd = Column(String(255))
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime)

    accounts = relationship("BackendAccount", back_populates="backend")
    automation_results = relationship("AutomationResult", back_populates="backend")
    logs = relationship("Log", back_populates="backend")


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
    logs = relationship("Log", back_populates="backend_accounts")

class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(Enum("error", "info", "warning", "debug"), nullable=False)
    description = Column(Text)
    source_url = Column(String(255))
    backend_id = Column(BigInteger, ForeignKey('backend_games.id'), nullable=True)
    account_id = Column(BigInteger, ForeignKey("backend_accounts.id"))
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())

    task_id = Column(
        String(36),
        ForeignKey("automation_results.task_id", ondelete="SET NULL", onupdate="CASCADE"),
        nullable=True,
        index=True,
    )

    backend = relationship("BackendGame", back_populates="logs")
    backend_accounts = relationship("BackendAccount", back_populates="logs")

    result = relationship(
        "AutomationResult",
        back_populates="logs",
        foreign_keys=[task_id],
        uselist=False,
    )

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
    referral_bonuses = relationship("ReferralBonus", back_populates="user")
    spins = relationship("WheelSpin", back_populates="user")
    wallet_master = relationship("WalletMaster", back_populates="user", uselist=False)

    # --- Convenience properties ---
    @property
    def balance_minor(self):
        return self.wallet_master.balance_minor if self.wallet_master else None
    @property
    def wallet_id(self):
        return self.wallet_master.id if self.wallet_master else None

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
    backend_id = Column(Integer, ForeignKey("backend_games.id"), nullable=True)
    status = Column(String(255), nullable=True, default="pending")
    screenshot_url = Column(String(255), nullable=True, default=None)
    data = Column(Text, nullable=True)
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    duration_seconds = Column(Float, nullable=True)

    order_id = Column(String(50), nullable=True, default=None)

    # Optional: relationship to User
    user = relationship("User", back_populates="automation_results")
    backend = relationship("BackendGame", back_populates="automation_results")
    requests = relationship(
        "AutomationRequest",
        back_populates="result",
        primaryjoin="AutomationResult.task_id==foreign(AutomationRequest.task_id)",
        cascade="all, delete-orphan",
        uselist=False,  # set to False if you GUARANTEE task_id is unique and want 1:1
    )

    logs = relationship(
        "Log",
        back_populates="result",
        cascade="save-update",  # don't delete logs implicitly; keep history
        passive_deletes=True,
    )

class BackendSession(Base):
    __tablename__ = 'backend_sessions'

    id = Column(Integer, primary_key=True, autoincrement=True)
    backend = Column(String(255), nullable=True)
    token = Column(Text, nullable=True)
    expires = Column(String(255), nullable=True)
    is_valid = Column(Boolean, nullable=False, default=True)
    active_tasks_count = Column(Integer, nullable=True)



class ReferralBonus(Base):
    __tablename__ = 'referral_bonuses'  # Update this to your actual table name

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    referrer_user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    referred_user_id = Column(BigInteger, nullable=False)
    payment_id = Column(BigInteger, nullable=True)
    bonus_percentage = Column(DECIMAL(5, 2), nullable=False)
    amount_loaded = Column(DECIMAL(10, 6), nullable=False)
    bonus_amount = Column(DECIMAL(10, 6), nullable=False)
    status = Column(String(255), nullable=False, default='pending')
    claimed_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, nullable=True, server_default=func.now())
    updated_at = Column(TIMESTAMP, nullable=True, onupdate=func.now())

    user = relationship("User", back_populates="referral_bonuses")


class WheelSpin(Base):
    __tablename__ = 'spins'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), nullable=False)
    spun_at = Column(TIMESTAMP, nullable=False)
    type = Column(String(255), nullable=False)
    ip_address = Column(String(45), nullable=True)
    reward = Column(DECIMAL(10, 6), nullable=True)
    status = Column(String(255), nullable=False, default="pending")
    created_at = Column(TIMESTAMP, nullable=True, server_default=func.now())
    updated_at = Column(TIMESTAMP, nullable=True, onupdate=func.now())

    user = relationship("User", back_populates="spins")


class RedeemRequest(Base):
    __tablename__ = 'redeem_requests'

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, nullable=False)
    username = Column(String(255), nullable=False)
    game_id = Column(BigInteger, nullable=False)
    payment_method = Column(String(255), nullable=False)
    payment_address = Column(String(255), nullable=True)
    network = Column(String(255), nullable=True)
    wallet_address = Column(String(255), nullable=True)
    amount = Column(DECIMAL(10, 6), nullable=False)
    tip = Column(DECIMAL(10, 6), nullable=False)
    status = Column(String(255), nullable=False, default='pending')
    reject_reason = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP, nullable=True)
    updated_at = Column(TIMESTAMP, nullable=True)


class AutomationRequest(Base):
    __tablename__ = "automation_requests"

    id = Column(BigInteger, primary_key=True, autoincrement=True)

    task_id = Column(
        CHAR(36),
        ForeignKey("automation_results.task_id", onupdate="CASCADE", ondelete="CASCADE"),
        nullable=False
    )

    type = Column(
        Enum("create", "recharge", "freeplay", "withdraw", "read", "reset-password", name="request_type"),
        nullable=False
    )

    payload = Column(JSON, nullable=True)

    created_at = Column(DateTime, server_default=func.now(), nullable=False)
    updated_at = Column(DateTime, server_default=func.now(), onupdate=func.now(), nullable=False)

    status_code = Column(Integer, nullable=True)
    result = relationship(
        "AutomationResult",
        back_populates="requests",
        primaryjoin="foreign(AutomationRequest.task_id)==AutomationResult.task_id",
    )

class PersonalAccessToken(Base):
    __tablename__ = "personal_access_tokens"

    id = Column(BigInteger, primary_key=True, autoincrement=True)
    tokenable_type = Column(String(255), nullable=False)
    tokenable_id = Column(BigInteger, nullable=False)
    name = Column(String(255), nullable=False)
    token = Column(String(64), unique=True, nullable=False)  # hashed token
    abilities = Column(Text, nullable=True)
    last_used_at = Column(TIMESTAMP, nullable=True)
    expires_at = Column(TIMESTAMP, nullable=True)
    created_at = Column(TIMESTAMP, nullable=True)
    updated_at = Column(TIMESTAMP, nullable=True)


class WalletMaster(Base):
    __tablename__ = "wallet_master"
    id = Column(BigInteger, primary_key=True, autoincrement=True)
    user_id = Column(BigInteger, ForeignKey("users.id"), unique=True, index=True, nullable=False)
    balance_minor = Column(DECIMAL(10, 6), nullable=False, default=0.000000)
    currency = Column(CHAR(3), nullable=False)
    created_at = Column(TIMESTAMP, server_default=func.now(), nullable=True)
    updated_at = Column(TIMESTAMP, server_default=func.now(), onupdate=func.now(), nullable=True)
    user = relationship("User", back_populates="wallet_master", uselist=False)