
import os
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker, scoped_session, declarative_base
from settings import DB_USER, DB_PASS, DB_HOST, DB_PORT, DB_NAME

SQLALCHEMY_DATABASE_URL = f"mysql+pymysql://{DB_USER}:{DB_PASS}@{DB_HOST}:{DB_PORT}/{DB_NAME}"

# NOTE: this engine is created at import time, i.e. in the Celery parent BEFORE
# it forks its prefork children. Inherited connections are NOT fork-safe, so the
# worker disposes the pool per child in celery_app.py (worker_process_init).
#
# Bounded pool + finite pool_timeout so contention surfaces as a fast error
# instead of an indefinitely-parked caller. pool_recycle avoids handing out
# connections the server has already dropped.
engine = create_engine(
    SQLALCHEMY_DATABASE_URL,
    pool_pre_ping=True,
    pool_size=5,
    max_overflow=10,
    pool_recycle=1800,
    pool_timeout=30,
)
SessionLocal = scoped_session(sessionmaker(autocommit=False, autoflush=False, bind=engine))

Base = declarative_base()
