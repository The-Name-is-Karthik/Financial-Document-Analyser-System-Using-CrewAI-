"""
worker.py — Celery worker for queue-based financial document analysis.

Bonus feature: handles concurrent analysis requests via Redis as a message
broker, with SQLite/PostgreSQL persistence for results.

To run the worker:
    celery -A worker worker --loglevel=info --concurrency=4

Requirements (add to requirements.txt or install manually):
    celery>=5.3.0
    redis>=5.0.0
"""

import json
import os
from datetime import datetime

from celery import Celery
from sqlalchemy.orm import Session

from database import AnalysisJob, SessionLocal, init_db

# ---------------------------------------------------------------------------
# Celery app configuration
# ---------------------------------------------------------------------------
REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379/0")

celery_app = Celery(
    "financial_analyzer",
    broker=REDIS_URL,
    backend=REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="UTC",
    enable_utc=True,
    # Retry failed tasks up to 3 times with exponential back-off
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    task_max_retries=3,
    # Keep results for 24 h
    result_expires=86400,
)

# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------

def _update_job(db: Session, job_id: str, **kwargs) -> None:
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
    if job:
        for k, v in kwargs.items():
            setattr(job, k, v)
        job.updated_at = datetime.utcnow()
        db.commit()


# ---------------------------------------------------------------------------
# Celery task
# ---------------------------------------------------------------------------

@celery_app.task(bind=True, name="analyze_document")
def analyze_document_task(self, job_id: str, query: str, file_path: str) -> dict:
    """
    Celery task that runs the CrewAI analysis pipeline for a given document.

    Args:
        job_id:    UUID of the AnalysisJob row in the database.
        query:     User's natural-language analysis query.
        file_path: Path to the uploaded PDF on the worker's filesystem.

    Returns:
        dict with keys: status, result (or error_message).
    """
    init_db()
    db = SessionLocal()

    try:
        _update_job(db, job_id, status="running")

        from router import get_active_crew
        from pdf_gen import generate_pdf
        import os

        crew = get_active_crew(query)

        result = crew.kickoff(inputs={"query": query, "file_path": file_path})
        result_str = str(result)

        # Automatically save PDF to outputs directory
        pdf_path = os.path.join("outputs", f"analysis_{job_id}.pdf")
        try:
            generate_pdf(result_str, pdf_path)
        except Exception:
            pass # Non-blocking if PDF gen fails

        _update_job(
            db,
            job_id,
            status="completed",
            result=json.dumps({"analysis": result_str}),
        )

        # Cleanup on success
        if os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass

        return {"status": "completed", "result": result_str}

    except Exception as exc:
        error_msg = str(exc)
        _update_job(db, job_id, status="failed", error_message=error_msg)

        # Retry with exponential back-off
        raise self.retry(exc=exc, countdown=2 ** self.request.retries)

    finally:
        db.close()
