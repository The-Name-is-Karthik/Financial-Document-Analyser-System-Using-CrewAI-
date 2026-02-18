"""
main.py — FastAPI application for the Financial Document Analyzer.

Fixes applied vs. original:
  - BUG FIX 15: Renamed imported task variable to avoid shadowing the FastAPI
    endpoint function `analyze_financial_document`.
  - BUG FIX 17: `file_path` is now passed through to the crew via the inputs dict.
  - BONUS: Database integration (SQLAlchemy) for job tracking.
  - BONUS: Intelligent Router added for dynamic, intent-based task execution.
  - BONUS: Optional async queue via Celery/Redis; falls back to synchronous
    execution when Redis is unavailable.
"""

import json
import os
import uuid
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from fastapi.responses import JSONResponse, FileResponse
from sqlalchemy.orm import Session

from database import AnalysisJob, init_db, get_db
from pdf_gen import generate_pdf

# ---------------------------------------------------------------------------
# Check whether the Celery worker / Redis is available
# ---------------------------------------------------------------------------
_USE_QUEUE = False
try:
    from worker import analyze_document_task, celery_app
    # Quick ping to confirm broker connectivity
    celery_app.control.inspect(timeout=1).ping()
    _USE_QUEUE = True
except Exception:
    _USE_QUEUE = False  # Graceful fallback to synchronous execution


# ---------------------------------------------------------------------------
# Synchronous (inline) crew runner — used when queue is unavailable
# ---------------------------------------------------------------------------
def run_crew_sync(query: str, file_path: str) -> str:
    """Run a dynamic subset of the CrewAI pipeline synchronously."""
    from router import get_active_crew
    crew = get_active_crew(query)
    result = crew.kickoff(inputs={"query": query, "file_path": file_path})
    return str(result)


# ---------------------------------------------------------------------------
# Application lifecycle
# ---------------------------------------------------------------------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield


app = FastAPI(
    title="Financial Document Analyzer",
    description="AI-powered financial document analysis using CrewAI agents.",
    version="2.0.0",
    lifespan=lifespan,
)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/", tags=["Health"])
async def root():
    """Health check endpoint."""
    return {
        "message": "Financial Document Analyzer API is running",
        "queue_enabled": _USE_QUEUE,
        "version": "2.0.0",
    }


@app.post("/analyze", tags=["Analysis"])
async def analyze_financial_document(           # name no longer collides with task import
    file: UploadFile = File(...),
    query: str = Form(default="Analyze this financial document and provide investment insights"),
    db: Session = Depends(get_db),
):
    """
    Upload a PDF financial document and receive an AI-powered analysis.

    - **file**: PDF file to analyse (multipart/form-data)
    - **query**: Natural-language question or analysis directive

    When Redis/Celery is available the job is queued and the endpoint returns
    immediately with a `job_id` that can be polled via `GET /jobs/{job_id}`.
    Otherwise the analysis runs synchronously and the result is returned directly.
    """
    job_id = None
    file_path = None

    if not file.filename.lower().endswith(".pdf"):
        raise HTTPException(status_code=400, detail="Only PDF files are supported.")

    try:
        job_id = str(uuid.uuid4())
        file_path = f"data/financial_document_{job_id}.pdf"
        os.makedirs("data", exist_ok=True)
        
        # RAM-efficient streaming write
        file_size = 0
        with open(file_path, "wb") as f:
            while chunk := await file.read(1024 * 1024): # 1MB chunks
                file_size += len(chunk)
                f.write(chunk)

        if file_size == 0:
            raise HTTPException(status_code=400, detail="Uploaded file is empty.")

        # Normalise query
        if not query or not query.strip():
            query = "Analyze this financial document and provide investment insights"
        query = query.strip()

        # Persist job record
        job = AnalysisJob(
            id=job_id,
            status="pending",
            filename=file.filename,
            query=query,
        )
        db.add(job)
        
        # Persist document metadata (Bonus feature completion)
        from database import DocumentRecord
        doc_record = DocumentRecord(
            job_id=job_id,
            original_filename=file.filename,
            stored_path=file_path,
            file_size_bytes=file_size,
        )
        db.add(doc_record)
        
        db.commit()

        if _USE_QUEUE:
            # ---------------------------------------------------------------
            # ASYNC PATH: dispatch to Celery worker and return immediately
            # ---------------------------------------------------------------
            analyze_document_task.apply_async(
                kwargs={"job_id": job_id, "query": query, "file_path": file_path},
                task_id=job_id,
            )
            return JSONResponse(
                status_code=202,
                content={
                    "status": "queued",
                    "job_id": job_id,
                    "message": "Analysis queued. Poll GET /jobs/{job_id} for results.",
                    "query": query,
                    "file_processed": file.filename,
                },
            )

        else:
            # ---------------------------------------------------------------
            # SYNC PATH: run crew inline, block until complete
            # ---------------------------------------------------------------
            job.status = "running"
            db.commit()

            analysis = run_crew_sync(query=query, file_path=file_path)

            # Automatically save PDF to outputs directory
            pdf_path = os.path.join("outputs", f"analysis_{job_id}.pdf")
            try:
                generate_pdf(analysis, pdf_path)
            except Exception:
                pass # Non-blocking if PDF gen fails

            job.status = "completed"
            job.result = json.dumps({"analysis": analysis})
            db.commit()

            return {
                "status": "success",
                "job_id": job_id,
                "query": query,
                "analysis": analysis,
                "file_processed": file.filename,
            }

    except HTTPException:
        raise
    except Exception as e:
        if job_id:
            job_record = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
            if job_record:
                job_record.status = "failed"
                job_record.error_message = str(e)
                db.commit()
        raise HTTPException(
            status_code=500,
            detail=f"Error processing financial document: {str(e)}",
        )
    finally:
        # Only clean up the file in the sync path; the worker does it in async path
        if not _USE_QUEUE and file_path and os.path.exists(file_path):
            try:
                os.remove(file_path)
            except OSError:
                pass


@app.get("/jobs/{job_id}", tags=["Jobs"])
async def get_job_status(job_id: str, db: Session = Depends(get_db)):
    """
    Poll the status of an analysis job (useful when queue mode is active).

    Returns the full analysis result once the job reaches `completed` status.
    """
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")

    response: dict = {
        "job_id": job.id,
        "status": job.status,
        "query": job.query,
        "filename": job.filename,
        "created_at": job.created_at.isoformat(),
        "updated_at": job.updated_at.isoformat(),
    }

    if job.status == "completed" and job.result:
        response["analysis"] = json.loads(job.result).get("analysis", "")
    elif job.status == "failed":
        response["error"] = job.error_message

    return response


@app.get("/jobs", tags=["Jobs"])
async def list_jobs(
    limit: int = 20,
    status: str = None,
    db: Session = Depends(get_db),
):
    """List recent analysis jobs, optionally filtered by status."""
    q = db.query(AnalysisJob)
    if status:
        q = q.filter(AnalysisJob.status == status)
    jobs = q.order_by(AnalysisJob.created_at.desc()).limit(limit).all()
    return [
        {
            "job_id": j.id,
            "status": j.status,
            "filename": j.filename,
            "query": j.query,
            "created_at": j.created_at.isoformat(),
        }
        for j in jobs
    ]


@app.get("/jobs/{job_id}/results/pdf", tags=["Results"])
async def download_analysis_pdf(job_id: str, db: Session = Depends(get_db)):
    """
    Generate and download the analysis result as a PDF file.
    """
    job = db.query(AnalysisJob).filter(AnalysisJob.id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail=f"Job '{job_id}' not found.")
    
    if job.status != "completed" or not job.result:
        raise HTTPException(status_code=400, detail="Analysis is not yet complete or has failed.")

    analysis_data = json.loads(job.result).get("analysis", "")
    if not analysis_data:
        raise HTTPException(status_code=500, detail="Analysis result is empty.")

    # Generate a unique temporary path for the PDF
    pdf_filename = f"analysis_{job_id}.pdf"
    pdf_path = os.path.join("data", pdf_filename)
    
    try:
        generate_pdf(analysis_data, pdf_path)
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to generate PDF: {str(e)}")

    return FileResponse(
        path=pdf_path,
        filename=f"Financial_Analysis_{job.filename or 'report'}.pdf",
        media_type="application/pdf"
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    import uvicorn
    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
