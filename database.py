"""
database.py — SQLAlchemy database models and session management.

Bonus feature: Persistent storage for analysis results, job status, and
uploaded document metadata.
"""

import os
from datetime import datetime

from sqlalchemy import (
    Column,
    DateTime,
    Integer,
    String,
    Text,
    Enum,
    create_engine,
    event,
)
from sqlalchemy.orm import declarative_base, sessionmaker

# ---------------------------------------------------------------------------
# Engine & Session
# ---------------------------------------------------------------------------
DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./financial_analyzer.db")

connect_args = {"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {}
engine = create_engine(DATABASE_URL, connect_args=connect_args)

# Enable WAL mode for SQLite so concurrent readers don't block the writer
if DATABASE_URL.startswith("sqlite"):
    @event.listens_for(engine, "connect")
    def set_sqlite_pragma(dbapi_connection, connection_record):
        cursor = dbapi_connection.cursor()
        cursor.execute("PRAGMA journal_mode=WAL")
        cursor.close()

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

# ---------------------------------------------------------------------------
# Models
# ---------------------------------------------------------------------------

class AnalysisJob(Base):
    """Represents a single document-analysis request."""

    __tablename__ = "analysis_jobs"

    id = Column(String(36), primary_key=True, index=True)
    status = Column(
        Enum("pending", "running", "completed", "failed", name="job_status"),
        default="pending",
        nullable=False,
        index=True,
    )
    filename = Column(String(255), nullable=True)
    query = Column(Text, nullable=False)
    result = Column(Text, nullable=True)        # JSON string of analysis output
    error_message = Column(Text, nullable=True)
    created_at = Column(DateTime, default=datetime.utcnow, nullable=False)
    updated_at = Column(DateTime, default=datetime.utcnow, onupdate=datetime.utcnow, nullable=False)


class DocumentRecord(Base):
    """Stores metadata about processed financial documents."""

    __tablename__ = "documents"

    id = Column(Integer, primary_key=True, autoincrement=True)
    job_id = Column(String(36), nullable=False, index=True)
    original_filename = Column(String(255), nullable=True)
    stored_path = Column(String(512), nullable=False)
    file_size_bytes = Column(Integer, nullable=True)
    document_type = Column(String(100), nullable=True)   # e.g. "annual_report", "earnings_release"
    issuer = Column(String(255), nullable=True)
    reporting_period = Column(String(100), nullable=True)
    uploaded_at = Column(DateTime, default=datetime.utcnow, nullable=False)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def init_db() -> None:
    """Create all tables if they do not yet exist."""
    Base.metadata.create_all(bind=engine)


def get_db():
    """FastAPI dependency that yields a DB session and ensures cleanup."""
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()
