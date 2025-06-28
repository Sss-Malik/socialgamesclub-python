# casino_automation/models.py
from sqlalchemy import Column, Integer, String, Text, Boolean, Enum, ForeignKey, DateTime, func
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
    user_id = Column(Integer, nullable=True)

    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
    deleted_at = Column(DateTime)

    backend = relationship("BackendGame", back_populates="accounts")


class Log(Base):
    __tablename__ = "logs"

    id = Column(Integer, primary_key=True, index=True)
    type = Column(Enum("error", "info", "warning", "debug"), nullable=False)
    description = Column(Text)
    source_url = Column(String(255))
    created_at = Column(DateTime, default=func.now())
    updated_at = Column(DateTime, default=func.now(), onupdate=func.now())
