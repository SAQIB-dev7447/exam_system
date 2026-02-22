"""Student portal routes: exam listing, instructions, attempt, submit, results."""

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, HTMLResponse, JSONResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError
from database import get_db
from models import Exam, Question, StudentSession, StudentResponse, AuditLog
from routers.auth import get_student_name, get_student_reg
from datetime import datetime, timezone, timedelta
from config import PASS_PERCENTAGE

router = APIRouter(prefix="/student")
templates = Jinja2Templates(directory="templates")


# ── Auth Guard ───────────────────────────────────────────────
def require_student(request: Request) -> dict:
    name = get_student_name(request)
    reg = get_student_reg(request)
    if not name or not reg:
        from fastapi import HTTPException
        raise HTTPException(status_code=303, headers={"Location": "/?error=Please+login+as+student"})
    return {"student_name": name, "registration_number": reg}


def _ensure_tz(dt):
    """Ensure a datetime has timezone info (UTC)."""
    if dt and dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


# ── Active Exams List ────────────────────────────────────────
@router.get("/exams", response_class=HTMLResponse)
async def list_exams(request: Request, db: Session = Depends(get_db)):
    student = require_student(request)

    exams = db.query(Exam).filter(Exam.status == "active").all()
    now = datetime.now(timezone.utc)

    active_exams = []
    for exam in exams:
        start = _ensure_tz(exam.start_time)
        end = _ensure_tz(exam.end_time)
        if start <= now <= end:
            # Check if student already has a session
            existing = db.query(StudentSession).filter(
                StudentSession.exam_id == exam.id,
                StudentSession.registration_number == student["registration_number"]
            ).first()

            exam_dict = {
                "id": str(exam.id),
                "title": exam.title,
                "description": exam.description,
                "duration_minutes": exam.duration_minutes,
                "total_marks": exam.total_marks,
                "already_submitted": bool(existing and existing.is_submitted),
                "session_exists": bool(existing),
            }
            active_exams.append(exam_dict)

    return templates.TemplateResponse("student_exam_list.html", {
        "request": request,
        "student": student,
        "exams": active_exams,
    })


# ── Instructions Page ────────────────────────────────────────
@router.get("/exam/{exam_id}/instructions", response_class=HTMLResponse)
async def instructions_page(request: Request, exam_id: str, db: Session = Depends(get_db)):
    student = require_student(request)

    exam = db.query(Exam).filter(Exam.id == exam_id, Exam.status == "active").first()
    if not exam:
        return RedirectResponse(url="/student/exams", status_code=303)

    now = datetime.now(timezone.utc)
    start = _ensure_tz(exam.start_time)
    end = _ensure_tz(exam.end_time)
    if now < start or now > end:
        return RedirectResponse(url="/student/exams", status_code=303)

    existing = db.query(StudentSession).filter(
        StudentSession.exam_id == exam_id,
        StudentSession.registration_number == student["registration_number"],
        StudentSession.is_submitted == True
    ).first()
    if existing:
        return RedirectResponse(url=f"/student/exam/{exam_id}/result", status_code=303)

    q_count = db.query(func.count(Question.id)).filter(Question.exam_id == exam_id).scalar() or 0

    exam_dict = {
        "id": str(exam.id), "title": exam.title, "description": exam.description,
        "duration_minutes": exam.duration_minutes, "total_marks": exam.total_marks,
    }

    return templates.TemplateResponse("exam_instructions_page.html", {
        "request": request,
        "student": student,
        "exam": exam_dict,
        "question_count": q_count,
    })


# ── Start Exam ───────────────────────────────────────────────
@router.post("/exam/{exam_id}/start")
async def start_exam(request: Request, exam_id: str, db: Session = Depends(get_db)):
    student = require_student(request)

    exam = db.query(Exam).filter(Exam.id == exam_id, Exam.status == "active").first()
    if not exam:
        return RedirectResponse(url="/student/exams", status_code=303)

    now = datetime.now(timezone.utc)
    start = _ensure_tz(exam.start_time)
    end = _ensure_tz(exam.end_time)
    if now < start or now > end:
        return RedirectResponse(url="/student/exams", status_code=303)

    # Check for existing session
    existing = db.query(StudentSession).filter(
        StudentSession.exam_id == exam_id,
        StudentSession.registration_number == student["registration_number"]
    ).first()

    if existing:
        if existing.is_submitted:
            return RedirectResponse(url=f"/student/exam/{exam_id}/result", status_code=303)
        session_id = str(existing.id)
    else:
        try:
            new_session = StudentSession(
                exam_id=exam_id,
                student_name=student["student_name"],
                registration_number=student["registration_number"],
                total_marks=exam.total_marks,
            )
            db.add(new_session)
            db.commit()
            db.refresh(new_session)
            session_id = str(new_session.id)

            # Audit log
            audit = AuditLog(
                session_id=new_session.id,
                action="exam_started",
                details=f"Student {student['student_name']} ({student['registration_number']}) started exam {exam.title}",
            )
            db.add(audit)
            db.commit()
        except IntegrityError:
            db.rollback()
            # Duplicate — fetch existing
            existing = db.query(StudentSession).filter(
                StudentSession.exam_id == exam_id,
                StudentSession.registration_number == student["registration_number"]
            ).first()
            if existing:
                session_id = str(existing.id)
            else:
                return RedirectResponse(url="/student/exams", status_code=303)

    response = RedirectResponse(url=f"/student/exam/{exam_id}/attempt", status_code=303)
    response.set_cookie("student_session_id", session_id, httponly=True, samesite="lax")
    response.set_cookie("student_exam_id", exam_id, httponly=True, samesite="lax")
    return response


# ── Exam Attempt Interface ───────────────────────────────────
@router.get("/exam/{exam_id}/attempt", response_class=HTMLResponse)
async def attempt_exam(request: Request, exam_id: str, db: Session = Depends(get_db)):
    student = require_student(request)
    session_id = request.cookies.get("student_session_id")

    if not session_id:
        return RedirectResponse(url=f"/student/exam/{exam_id}/instructions", status_code=303)

    session = db.query(StudentSession).filter(
        StudentSession.id == session_id, StudentSession.exam_id == exam_id
    ).first()
    if not session:
        return RedirectResponse(url="/student/exams", status_code=303)
    if session.is_submitted:
        return RedirectResponse(url=f"/student/exam/{exam_id}/result", status_code=303)

    exam = db.query(Exam).filter(Exam.id == exam_id).first()
    if not exam:
        return RedirectResponse(url="/student/exams", status_code=303)

    now = datetime.now(timezone.utc)
    end_time = _ensure_tz(exam.end_time)
    if now > end_time:
        return RedirectResponse(url=f"/student/exam/{exam_id}/submit?auto=1", status_code=303)

    # Calculate remaining time based on BOTH exam end time and duration
    started_at = _ensure_tz(session.started_at)
    duration_end = started_at + timedelta(minutes=exam.duration_minutes)
    actual_end = min(end_time, duration_end)
    remaining_seconds = max(0, int((actual_end - now).total_seconds()))

    # Get questions
    questions = db.query(Question).filter(Question.exam_id == exam_id).order_by(Question.order_num).all()

    # Get existing answers
    responses = db.query(StudentResponse).filter(StudentResponse.session_id == session_id).all()
    answers = {str(r.question_id): r.selected_option for r in responses}

    # Current question
    current_q = int(request.query_params.get("q", "1"))
    current_q = max(1, min(current_q, len(questions)))

    # Convert to dicts
    q_dicts = []
    for q in questions:
        q_dicts.append({
            "id": str(q.id), "question_text": q.question_text,
            "option_a": q.option_a, "option_b": q.option_b,
            "option_c": q.option_c, "option_d": q.option_d,
            "marks": q.marks, "order_num": q.order_num,
        })

    exam_dict = {
        "id": str(exam.id), "title": exam.title,
        "duration_minutes": exam.duration_minutes, "total_marks": exam.total_marks,
    }

    return templates.TemplateResponse("student_exam_interface.html", {
        "request": request,
        "student": student,
        "exam": exam_dict,
        "session": {"id": str(session.id), "started_at": session.started_at.isoformat() if session.started_at else ""},
        "questions": q_dicts,
        "answers": answers,
        "current_q": current_q,
        "total_questions": len(q_dicts),
        "remaining_seconds": remaining_seconds,
        "end_timestamp": actual_end.isoformat(),
    })


# ── Save Answer (AJAX) ──────────────────────────────────────
@router.post("/exam/{exam_id}/save-answer")
async def save_answer(request: Request, exam_id: str, db: Session = Depends(get_db)):
    student = require_student(request)
    session_id = request.cookies.get("student_session_id")

    if not session_id:
        return JSONResponse({"error": "No active session"}, status_code=401)

    session = db.query(StudentSession).filter(StudentSession.id == session_id).first()
    if not session or session.is_submitted:
        return JSONResponse({"error": "Exam already submitted"}, status_code=400)

    try:
        body = await request.json()
        question_id = body.get("question_id")
        selected_option = body.get("selected_option", "").upper()

        if not question_id or selected_option not in ("A", "B", "C", "D"):
            return JSONResponse({"error": "Invalid data"}, status_code=400)

        question = db.query(Question).filter(Question.id == question_id, Question.exam_id == exam_id).first()
        if not question:
            return JSONResponse({"error": "Invalid question"}, status_code=400)

        is_correct = selected_option == question.correct_option

        # Upsert: check if response exists
        existing_resp = db.query(StudentResponse).filter(
            StudentResponse.session_id == session_id,
            StudentResponse.question_id == question_id
        ).first()

        if existing_resp:
            existing_resp.selected_option = selected_option
            existing_resp.is_correct = is_correct
            existing_resp.saved_at = datetime.now(timezone.utc)
        else:
            new_resp = StudentResponse(
                session_id=session_id,
                question_id=question_id,
                selected_option=selected_option,
                is_correct=is_correct,
            )
            db.add(new_resp)

        db.commit()
        return JSONResponse({"status": "saved", "is_correct": is_correct})
    except Exception as e:
        db.rollback()
        print(f"Save answer error: {e}")
        return JSONResponse({"error": "Failed to save"}, status_code=500)


# ── Submit Exam (atomic transaction) ─────────────────────────
@router.post("/exam/{exam_id}/submit")
async def submit_exam(request: Request, exam_id: str, db: Session = Depends(get_db)):
    student = require_student(request)
    session_id = request.cookies.get("student_session_id")

    if not session_id:
        return RedirectResponse(url="/student/exams", status_code=303)

    session = db.query(StudentSession).filter(
        StudentSession.id == session_id, StudentSession.exam_id == exam_id
    ).first()
    if not session:
        return RedirectResponse(url="/student/exams", status_code=303)
    if session.is_submitted:
        return RedirectResponse(url=f"/student/exam/{exam_id}/result", status_code=303)

    try:
        # Calculate score inside a single transaction
        responses = db.query(StudentResponse).filter(StudentResponse.session_id == session_id).all()

        total_score = 0
        for resp in responses:
            if resp.is_correct:
                q = db.query(Question).filter(Question.id == resp.question_id).first()
                if q:
                    total_score += q.marks

        exam = db.query(Exam).filter(Exam.id == exam_id).first()
        total_marks = exam.total_marks if exam else 0
        percentage = round((total_score / total_marks) * 100, 2) if total_marks > 0 else 0

        # Atomic update: only update if not yet submitted
        now = datetime.now(timezone.utc)
        session.is_submitted = True
        session.submitted_at = now
        session.score = total_score
        session.total_marks = total_marks
        session.percentage = percentage

        # Audit log
        audit = AuditLog(
            session_id=session.id,
            action="exam_submitted",
            details=f"Score: {total_score}/{total_marks} ({percentage}%)",
        )
        db.add(audit)

        db.commit()

        return RedirectResponse(url=f"/student/exam/{exam_id}/result", status_code=303)
    except Exception as e:
        db.rollback()
        print(f"Submit exam error: {e}")
        return RedirectResponse(url=f"/student/exam/{exam_id}/attempt", status_code=303)


# Auto-submit via GET (for timer expiry)
@router.get("/exam/{exam_id}/submit")
async def auto_submit_exam(request: Request, exam_id: str, db: Session = Depends(get_db)):
    student = require_student(request)
    session_id = request.cookies.get("student_session_id")

    if not session_id:
        return RedirectResponse(url="/student/exams", status_code=303)

    session = db.query(StudentSession).filter(StudentSession.id == session_id).first()
    if session and session.is_submitted:
        return RedirectResponse(url=f"/student/exam/{exam_id}/result", status_code=303)

    return HTMLResponse(f"""
    <html><body>
    <p>Auto-submitting exam...</p>
    <form id="f" method="POST" action="/student/exam/{exam_id}/submit">
    </form>
    <script>document.getElementById('f').submit();</script>
    </body></html>
    """)


# ── Result Page ──────────────────────────────────────────────
@router.get("/exam/{exam_id}/result", response_class=HTMLResponse)
async def result_page(request: Request, exam_id: str, db: Session = Depends(get_db)):
    student = require_student(request)

    session = db.query(StudentSession).filter(
        StudentSession.exam_id == exam_id,
        StudentSession.registration_number == student["registration_number"]
    ).first()
    if not session or not session.is_submitted:
        return RedirectResponse(url="/student/exams", status_code=303)

    exam = db.query(Exam).filter(Exam.id == exam_id).first()
    if not exam:
        return RedirectResponse(url="/student/exams", status_code=303)

    questions = db.query(Question).filter(Question.exam_id == exam_id).order_by(Question.order_num).all()
    responses = db.query(StudentResponse).filter(StudentResponse.session_id == session.id).all()
    answers = {}
    for r in responses:
        answers[str(r.question_id)] = {
            "selected_option": r.selected_option,
            "is_correct": r.is_correct,
        }

    correct_count = len([r for r in responses if r.is_correct])
    wrong_count = len([r for r in responses if not r.is_correct])
    unanswered = len(questions) - len(responses)

    passed = session.percentage is not None and float(session.percentage) >= PASS_PERCENTAGE

    # Convert to dicts
    q_dicts = []
    for q in questions:
        q_dicts.append({
            "id": str(q.id), "question_text": q.question_text,
            "option_a": q.option_a, "option_b": q.option_b,
            "option_c": q.option_c, "option_d": q.option_d,
            "correct_option": q.correct_option, "marks": q.marks,
        })

    session_dict = {
        "id": str(session.id), "score": session.score, "total_marks": session.total_marks,
        "percentage": float(session.percentage) if session.percentage else 0,
        "submitted_at": session.submitted_at.isoformat() if session.submitted_at else "",
    }

    exam_dict = {
        "id": str(exam.id), "title": exam.title,
        "duration_minutes": exam.duration_minutes, "total_marks": exam.total_marks,
    }

    return templates.TemplateResponse("student_result_page.html", {
        "request": request,
        "student": student,
        "session": session_dict,
        "exam": exam_dict,
        "questions": q_dicts,
        "answers": answers,
        "correct_count": correct_count,
        "wrong_count": wrong_count,
        "unanswered": unanswered,
        "passed": passed,
        "pass_percentage": PASS_PERCENTAGE,
    })
