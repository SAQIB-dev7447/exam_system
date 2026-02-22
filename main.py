"""Online Technical Examination System - FastAPI Application Entry Point."""

import uvicorn
from fastapi import FastAPI, Request, HTTPException
from fastapi.staticfiles import StaticFiles
from fastapi.responses import RedirectResponse
from sqlalchemy import text

from config import PORT
from database import engine, Base
from models import Faculty, Exam, Question, StudentSession, StudentResponse, AuditLog  # noqa: F401
from routers import auth, faculty, student

# Create all tables on startup
Base.metadata.create_all(bind=engine)

app = FastAPI(
    title="Online Technical Examination System",
    description="Production-ready examination system deployed on Render with Supabase PostgreSQL.",
    version="1.0.0",
)

# Mount static files
app.mount("/static", StaticFiles(directory="static"), name="static")

# Include routers
app.include_router(auth.router)
app.include_router(faculty.router)
app.include_router(student.router)


# ── Health Check (lightweight, for Render + GitHub Actions ping) ──
@app.get("/health")
def health_check():
    try:
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
        return {"status": "ok"}
    except Exception:
        raise HTTPException(status_code=500, detail="Database not reachable")


# Exception handler to redirect auth errors
@app.exception_handler(303)
async def auth_redirect_handler(request: Request, exc):
    location = getattr(exc, "headers", {}).get("Location", "/")
    return RedirectResponse(url=location, status_code=303)


if __name__ == "__main__":
    # Use PORT environment variable for Render
    port = int(os.environ.get("PORT", 8000))
    uvicorn.run("main:app", host="0.0.0.0", port=port, reload=False)
