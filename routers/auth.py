"""Authentication routes: login, logout for faculty and students."""

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, HTMLResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from database import get_db
from models import Faculty
import bcrypt

router = APIRouter()
templates = Jinja2Templates(directory="templates")


# ── Helpers ──────────────────────────────────────────────────
def set_session(response: RedirectResponse, key: str, value: str):
    """Set a signed cookie for session management."""
    response.set_cookie(key=key, value=str(value), httponly=True, samesite="lax", max_age=86400)
    return response


def get_faculty_id(request: Request) -> str | None:
    return request.cookies.get("faculty_id")


def get_student_name(request: Request) -> str | None:
    return request.cookies.get("student_name")


def get_student_reg(request: Request) -> str | None:
    return request.cookies.get("student_reg")


# ── Login Page ───────────────────────────────────────────────
@router.get("/", response_class=HTMLResponse)
async def login_page(request: Request):
    error = request.query_params.get("error", "")
    success = request.query_params.get("success", "")
    return templates.TemplateResponse("system_login_page.html", {
        "request": request,
        "error": error,
        "success": success,
    })


# ── Faculty Login ────────────────────────────────────────────
@router.post("/login/faculty")
async def faculty_login(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    db: Session = Depends(get_db),
):
    try:
        faculty = db.query(Faculty).filter(Faculty.username == username).first()
        if not faculty:
            return RedirectResponse(url="/?error=Invalid+username+or+password", status_code=303)

        if not bcrypt.checkpw(password.encode("utf-8"), faculty.password_hash.encode("utf-8")):
            return RedirectResponse(url="/?error=Invalid+username+or+password", status_code=303)

        response = RedirectResponse(url="/faculty/dashboard", status_code=303)
        set_session(response, "faculty_id", str(faculty.id))
        set_session(response, "faculty_name", faculty.full_name)
        set_session(response, "faculty_dept", faculty.department or "")
        return response
    except Exception as e:
        print(f"Faculty login error: {e}")
        return RedirectResponse(url="/?error=Login+failed.+Please+try+again.", status_code=303)


# ── Student Login ────────────────────────────────────────────
@router.post("/login/student")
async def student_login(
    request: Request,
    student_name: str = Form(...),
    registration_number: str = Form(...)
):
    try:
        response = RedirectResponse(url="/student/exams", status_code=303)
        set_session(response, "student_name", student_name.strip())
        set_session(response, "student_reg", registration_number.strip())
        return response
    except Exception as e:
        print(f"Student login error: {e}")
        return RedirectResponse(url="/?error=Login+failed.+Please+try+again.", status_code=303)


# ── Logout ───────────────────────────────────────────────────
@router.get("/logout")
async def logout(request: Request):
    response = RedirectResponse(url="/?success=Logged+out+successfully", status_code=303)
    for key in ["faculty_id", "faculty_name", "faculty_dept", "student_name", "student_reg", "student_session_id", "student_exam_id"]:
        response.delete_cookie(key)
    return response


# ── Faculty Registration (utility route for setup) ──────────
@router.get("/register/faculty", response_class=HTMLResponse)
async def register_page(request: Request):
    return templates.TemplateResponse("faculty_register.html", {
        "request": request,
        "error": request.query_params.get("error", ""),
        "success": request.query_params.get("success", ""),
    })


@router.post("/register/faculty")
async def register_faculty(
    request: Request,
    username: str = Form(...),
    password: str = Form(...),
    full_name: str = Form(...),
    department: str = Form(""),
    db: Session = Depends(get_db),
):
    try:
        existing = db.query(Faculty).filter(Faculty.username == username).first()
        if existing:
            return RedirectResponse(url="/register/faculty?error=Username+already+exists", status_code=303)

        password_hash = bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")
        new_faculty = Faculty(
            username=username,
            full_name=full_name,
            password_hash=password_hash,
            department=department,
        )
        db.add(new_faculty)
        db.commit()
        return RedirectResponse(url="/?success=Faculty+account+created.+Please+login.", status_code=303)
    except Exception as e:
        db.rollback()
        print(f"Registration error: {e}")
        return RedirectResponse(url="/register/faculty?error=Registration+failed", status_code=303)
