"""
Database layer for the WLB ERP.

SQLAlchemy. Defaults to a local SQLite file (erp.db next to this module).
For a public website set WLB_DATABASE_URL to a PostgreSQL URL; hosts that hand
out "postgres://..." or "postgresql://..." URLs are normalised automatically.

Tables
  users        login accounts (role: admin | hr | employee) + ERP profile fields
  assessments  every prediction, linked to the user who submitted it
"""
import os
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import (
    create_engine, Column, Integer, Float, String, DateTime, Boolean, ForeignKey,
)
from sqlalchemy.orm import declarative_base, sessionmaker, relationship

BASE_DIR = Path(__file__).resolve().parent


def normalize_db_url(url: str) -> str:
    """Render/Heroku style URLs use postgres:// or postgresql://; SQLAlchemy needs a driver name."""
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg2://" + url[len("postgresql://"):]
    return url


DATABASE_URL = normalize_db_url(os.getenv("WLB_DATABASE_URL") or f"sqlite:///{BASE_DIR / 'erp.db'}")
_is_sqlite = DATABASE_URL.startswith("sqlite")

engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if _is_sqlite else {},
    pool_pre_ping=True,          # survive idle connections dropped by the host
    pool_recycle=1800,
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


def utcnow() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    username = Column(String, unique=True, nullable=False, index=True)
    full_name = Column(String, nullable=False)
    email = Column(String, nullable=True)
    password_hash = Column(String, nullable=False)
    role = Column(String, nullable=False, default="employee")   # admin | hr | employee
    department = Column(String, nullable=False, default="General")
    designation = Column(String, nullable=False, default="")
    is_active = Column(Boolean, nullable=False, default=True)
    must_change_password = Column(Boolean, nullable=False, default=False)
    created_at = Column(DateTime, default=utcnow)
    last_login = Column(DateTime, nullable=True)

    assessments = relationship("Assessment", back_populates="user")


class Assessment(Base):
    """One submitted set of parameters + the model's prediction."""
    __tablename__ = "assessments"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=False, index=True)
    created_at = Column(DateTime, default=utcnow, index=True)

    # inputs
    work_hours = Column(Float, nullable=False)
    commute = Column(Float, nullable=False)
    sleep = Column(Float, nullable=False)
    stress = Column(Float, nullable=False)
    family_time = Column(Float, nullable=False)
    social_time = Column(Float, nullable=False)
    satisfaction = Column(Float, nullable=False)
    mental_health = Column(Float, nullable=False)

    # model output
    risk_label = Column(String, nullable=False)
    risk_score = Column(Float, nullable=False)      # 0..1, higher = better balance
    top_factor = Column(String, nullable=False)

    user = relationship("User", back_populates="assessments")


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
