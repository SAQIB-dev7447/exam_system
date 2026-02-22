"""Faculty portal routes: dashboard, exam CRUD, questions, monitoring, results."""

from fastapi import APIRouter, Request, Form, Depends
from fastapi.responses import RedirectResponse, HTMLResponse, StreamingResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy.orm import Session
from sqlalchemy import func, desc
from database import get_db
from models import Faculty, Exam, Question, StudentSession, StudentResponse
from routers.auth import get_faculty_id
from datetime import datetime, timezone
from config import PASS_PERCENTAGE
import csv
import io

router = APIRouter(prefix="/faculty")
templates = Jinja2Templates(directory="templates")


# ── Auth Guard ───────────────────────────────────────────────
def require_faculty(request: Request) -> dict:
    faculty_id = get_faculty_id(request)
    if not faculty_id:
        from fastapi import HTTPException
        raise HTTPException(status_code=303, headers={"Location": "/?error=Please+login+as+faculty"})
    return {
        "id": faculty_id,
        "full_name": request.cookies.get("faculty_name", "Faculty"),
        "department": request.cookies.get("faculty_dept", ""),
    }


# ── Dashboard ────────────────────────────────────────────────
@router.get("/dashboard", response_class=HTMLResponse)
async def dashboard(request: Request, db: Session = Depends(get_db)):
    faculty = require_faculty(request)

    exams = db.query(Exam).filter(Exam.faculty_id == faculty["id"]).order_by(desc(Exam.created_at)).all()

    total_exams = len(exams)
    active_exams = len([e for e in exams if e.status == "active"])
    draft_exams = len([e for e in exams if e.status == "draft"])
    completed_exams = len([e for e in exams if e.status == "completed"])

    # Count total students across all exams
    exam_ids = [str(e.id) for e in exams]
    total_students = 0
    if exam_ids:
        total_students = db.query(func.count(StudentSession.id)).filter(
            StudentSession.exam_id.in_(exam_ids)
        ).scalar() or 0

    # Auto-complete exams past end_time
    now = datetime.now(timezone.utc)
    for exam in exams:
        if exam.status == "active" and exam.end_time:
            if now > exam.end_time.replace(tzinfo=timezone.utc) if exam.end_time.tzinfo is None else now > exam.end_time:
                exam.status = "completed"
    db.commit()

    recent_exams = exams[:10]

    # Convert to dicts for template compatibility
    exam_dicts = []
    for e in recent_exams:
        exam_dicts.append({
            "id": str(e.id),
            "title": e.title,
            "status": e.status,
            "duration_minutes": e.duration_minutes,
            "created_at": e.created_at.isoformat() if e.created_at else "",
        })

    return templates.TemplateResponse("faculty_dashboard.html", {
        "request": request,
        "faculty": faculty,
        "total_exams": total_exams,
        "active_exams": active_exams,
        "draft_exams": draft_exams,
        "completed_exams": completed_exams,
        "total_students": total_students,
        "exams": exam_dicts,
    })


# ── Create Exam ──────────────────────────────────────────────
@router.get("/exams/create", response_class=HTMLResponse)
async def create_exam_page(request: Request):
    faculty = require_faculty(request)
    return templates.TemplateResponse("exam_creation_interface.html", {
        "request": request,
        "faculty": faculty,
        "error": request.query_params.get("error", ""),
    })


@router.post("/exams/create")
async def create_exam(
    request: Request,
    title: str = Form(...),
    description: str = Form(""),
    duration_minutes: int = Form(...),
    total_marks: int = Form(...),
    start_time: str = Form(...),
    end_time: str = Form(...),
    action: str = Form("save"),
    db: Session = Depends(get_db),
):
    faculty = require_faculty(request)

    try:
        status = "active" if action == "publish" else "draft"

        new_exam = Exam(
            faculty_id=faculty["id"],
            title=title.strip(),
            description=description.strip(),
            duration_minutes=duration_minutes,
            total_marks=total_marks,
            start_time=datetime.fromisoformat(start_time),
            end_time=datetime.fromisoformat(end_time),
            status=status,
        )
        db.add(new_exam)
        db.commit()
        db.refresh(new_exam)

        return RedirectResponse(url=f"/faculty/exams/{new_exam.id}/questions", status_code=303)
    except Exception as e:
        db.rollback()
        print(f"Create exam error: {e}")
        return RedirectResponse(url="/faculty/exams/create?error=Failed+to+create+exam", status_code=303)


# ── Question Management ─────────────────────────────────────
@router.get("/exams/{exam_id}/questions", response_class=HTMLResponse)
async def questions_page(request: Request, exam_id: str, db: Session = Depends(get_db)):
    faculty = require_faculty(request)

    exam = db.query(Exam).filter(Exam.id == exam_id, Exam.faculty_id == faculty["id"]).first()
    if not exam:
        return RedirectResponse(url="/faculty/dashboard", status_code=303)

    questions = db.query(Question).filter(Question.exam_id == exam_id).order_by(Question.order_num).all()

    # Convert to dicts for template
    exam_dict = {
        "id": str(exam.id), "title": exam.title, "status": exam.status,
        "duration_minutes": exam.duration_minutes, "total_marks": exam.total_marks,
    }
    q_dicts = []
    for q in questions:
        q_dicts.append({
            "id": str(q.id), "question_text": q.question_text,
            "option_a": q.option_a, "option_b": q.option_b,
            "option_c": q.option_c, "option_d": q.option_d,
            "correct_option": q.correct_option, "marks": q.marks,
            "order_num": q.order_num,
        })

    return templates.TemplateResponse("mcq_question_builder.html", {
        "request": request,
        "faculty": faculty,
        "exam": exam_dict,
        "questions": q_dicts,
        "error": request.query_params.get("error", ""),
        "success": request.query_params.get("success", ""),
        "is_locked": exam.status != "draft",
    })


@router.post("/exams/{exam_id}/questions/add")
async def add_question(
    request: Request,
    exam_id: str,
    question_text: str = Form(...),
    option_a: str = Form(...),
    option_b: str = Form(...),
    option_c: str = Form(...),
    option_d: str = Form(...),
    correct_option: str = Form(...),
    marks: int = Form(1),
    db: Session = Depends(get_db),
):
    faculty = require_faculty(request)

    exam = db.query(Exam).filter(Exam.id == exam_id, Exam.faculty_id == faculty["id"]).first()
    if not exam or exam.status != "draft":
        return RedirectResponse(url=f"/faculty/exams/{exam_id}/questions?error=Cannot+add+questions+to+published+exam", status_code=303)

    # Get next order number
    max_order = db.query(func.max(Question.order_num)).filter(Question.exam_id == exam_id).scalar()
    next_order = (max_order + 1) if max_order else 1

    try:
        new_q = Question(
            exam_id=exam_id,
            question_text=question_text.strip(),
            option_a=option_a.strip(),
            option_b=option_b.strip(),
            option_c=option_c.strip(),
            option_d=option_d.strip(),
            correct_option=correct_option.upper(),
            marks=marks,
            order_num=next_order,
        )
        db.add(new_q)
        db.commit()
        return RedirectResponse(url=f"/faculty/exams/{exam_id}/questions?success=Question+added+successfully", status_code=303)
    except Exception as e:
        db.rollback()
        print(f"Add question error: {e}")
        return RedirectResponse(url=f"/faculty/exams/{exam_id}/questions?error=Failed+to+add+question", status_code=303)


@router.post("/exams/{exam_id}/questions/{question_id}/edit")
async def edit_question(
    request: Request,
    exam_id: str,
    question_id: str,
    question_text: str = Form(...),
    option_a: str = Form(...),
    option_b: str = Form(...),
    option_c: str = Form(...),
    option_d: str = Form(...),
    correct_option: str = Form(...),
    marks: int = Form(1),
    db: Session = Depends(get_db),
):
    faculty = require_faculty(request)

    exam = db.query(Exam).filter(Exam.id == exam_id, Exam.faculty_id == faculty["id"]).first()
    if not exam or exam.status != "draft":
        return RedirectResponse(url=f"/faculty/exams/{exam_id}/questions?error=Cannot+edit+published+exam", status_code=303)

    try:
        q = db.query(Question).filter(Question.id == question_id, Question.exam_id == exam_id).first()
        if q:
            q.question_text = question_text.strip()
            q.option_a = option_a.strip()
            q.option_b = option_b.strip()
            q.option_c = option_c.strip()
            q.option_d = option_d.strip()
            q.correct_option = correct_option.upper()
            q.marks = marks
            db.commit()
        return RedirectResponse(url=f"/faculty/exams/{exam_id}/questions?success=Question+updated", status_code=303)
    except Exception as e:
        db.rollback()
        print(f"Edit question error: {e}")
        return RedirectResponse(url=f"/faculty/exams/{exam_id}/questions?error=Failed+to+edit+question", status_code=303)


@router.post("/exams/{exam_id}/questions/{question_id}/delete")
async def delete_question(request: Request, exam_id: str, question_id: str, db: Session = Depends(get_db)):
    faculty = require_faculty(request)

    exam = db.query(Exam).filter(Exam.id == exam_id, Exam.faculty_id == faculty["id"]).first()
    if not exam or exam.status != "draft":
        return RedirectResponse(url=f"/faculty/exams/{exam_id}/questions?error=Cannot+delete+from+published+exam", status_code=303)

    try:
        q = db.query(Question).filter(Question.id == question_id, Question.exam_id == exam_id).first()
        if q:
            db.delete(q)
            db.commit()
        return RedirectResponse(url=f"/faculty/exams/{exam_id}/questions?success=Question+deleted", status_code=303)
    except Exception as e:
        db.rollback()
        print(f"Delete question error: {e}")
        return RedirectResponse(url=f"/faculty/exams/{exam_id}/questions?error=Failed+to+delete+question", status_code=303)


# ── Publish Exam ─────────────────────────────────────────────
@router.post("/exams/{exam_id}/publish")
async def publish_exam(request: Request, exam_id: str, db: Session = Depends(get_db)):
    faculty = require_faculty(request)

    exam = db.query(Exam).filter(Exam.id == exam_id, Exam.faculty_id == faculty["id"]).first()
    if not exam:
        return RedirectResponse(url="/faculty/dashboard", status_code=303)
    if exam.status != "draft":
        return RedirectResponse(url=f"/faculty/exams/{exam_id}/questions?error=Exam+is+already+published", status_code=303)

    q_count = db.query(func.count(Question.id)).filter(Question.exam_id == exam_id).scalar() or 0
    if q_count == 0:
        return RedirectResponse(url=f"/faculty/exams/{exam_id}/questions?error=Add+at+least+one+question+before+publishing", status_code=303)

    try:
        exam.status = "active"
        db.commit()
        return RedirectResponse(url="/faculty/dashboard", status_code=303)
    except Exception as e:
        db.rollback()
        print(f"Publish exam error: {e}")
        return RedirectResponse(url=f"/faculty/exams/{exam_id}/questions?error=Failed+to+publish+exam", status_code=303)


# ── Emergency Stop ───────────────────────────────────────────
@router.post("/exams/{exam_id}/stop")
async def stop_exam(request: Request, exam_id: str, db: Session = Depends(get_db)):
    faculty = require_faculty(request)
    try:
        exam = db.query(Exam).filter(Exam.id == exam_id, Exam.faculty_id == faculty["id"]).first()
        if exam:
            exam.status = "completed"
            db.commit()
        return RedirectResponse(url="/faculty/dashboard", status_code=303)
    except Exception as e:
        db.rollback()
        print(f"Stop exam error: {e}")
        return RedirectResponse(url="/faculty/dashboard", status_code=303)


# ── Live Monitoring ──────────────────────────────────────────
@router.get("/exams/{exam_id}/monitor", response_class=HTMLResponse)
async def monitor_exam(request: Request, exam_id: str, db: Session = Depends(get_db)):
    faculty = require_faculty(request)

    exam = db.query(Exam).filter(Exam.id == exam_id, Exam.faculty_id == faculty["id"]).first()
    if not exam:
        return RedirectResponse(url="/faculty/dashboard", status_code=303)

    all_sessions = db.query(StudentSession).filter(StudentSession.exam_id == exam_id).all()

    total_students = len(all_sessions)
    submitted = len([s for s in all_sessions if s.is_submitted])
    in_progress = total_students - submitted

    now = datetime.now(timezone.utc)
    end_time = exam.end_time.replace(tzinfo=timezone.utc) if exam.end_time.tzinfo is None else exam.end_time
    remaining_seconds = max(0, int((end_time - now).total_seconds()))

    # Convert sessions to dicts
    session_dicts = []
    for s in all_sessions:
        session_dicts.append({
            "student_name": s.student_name,
            "registration_number": s.registration_number,
            "started_at": s.started_at.isoformat() if s.started_at else "",
            "is_submitted": s.is_submitted,
        })

    exam_dict = {
        "id": str(exam.id), "title": exam.title, "status": exam.status,
        "end_time": exam.end_time.isoformat() if exam.end_time else "",
    }

    return templates.TemplateResponse("exam_monitor.html", {
        "request": request,
        "faculty": faculty,
        "exam": exam_dict,
        "total_students": total_students,
        "submitted": submitted,
        "in_progress": in_progress,
        "remaining_seconds": remaining_seconds,
        "sessions": session_dicts,
    })


# ── Results Management ───────────────────────────────────────
@router.get("/exams/{exam_id}/results", response_class=HTMLResponse)
async def exam_results(request: Request, exam_id: str, db: Session = Depends(get_db)):
    faculty = require_faculty(request)

    exam = db.query(Exam).filter(Exam.id == exam_id, Exam.faculty_id == faculty["id"]).first()
    if not exam:
        return RedirectResponse(url="/faculty/dashboard", status_code=303)

    all_sessions = db.query(StudentSession).filter(StudentSession.exam_id == exam_id).order_by(desc(StudentSession.submitted_at)).all()

    total_students = len(all_sessions)
    submitted_sessions = [s for s in all_sessions if s.is_submitted]
    scores = [float(s.percentage) for s in submitted_sessions if s.percentage is not None]
    avg_score = round(sum(scores) / len(scores), 1) if scores else 0
    passed = len([s for s in scores if s >= PASS_PERCENTAGE])
    pass_rate = round((passed / len(scores)) * 100, 1) if scores else 0

    # Filter support
    status_filter = request.query_params.get("status", "")
    search = request.query_params.get("search", "")

    filtered = submitted_sessions
    if status_filter == "passed":
        filtered = [s for s in filtered if s.percentage is not None and float(s.percentage) >= PASS_PERCENTAGE]
    elif status_filter == "failed":
        filtered = [s for s in filtered if s.percentage is None or float(s.percentage) < PASS_PERCENTAGE]

    if search:
        search_lower = search.lower()
        filtered = [s for s in filtered
                    if search_lower in s.student_name.lower()
                    or search_lower in s.registration_number.lower()]

    # Convert to dicts
    student_dicts = []
    for s in filtered:
        student_dicts.append({
            "student_name": s.student_name,
            "registration_number": s.registration_number,
            "score": s.score or 0,
            "total_marks": s.total_marks or 0,
            "percentage": float(s.percentage) if s.percentage else 0,
            "submitted_at": s.submitted_at.isoformat() if s.submitted_at else "",
        })

    exam_dict = {"id": str(exam.id), "title": exam.title, "status": exam.status}

    return templates.TemplateResponse("faculty_result_management.html", {
        "request": request,
        "faculty": faculty,
        "exam": exam_dict,
        "students": student_dicts,
        "total_students": total_students,
        "avg_score": avg_score,
        "pass_rate": pass_rate,
        "passed_count": passed,
        "failed_count": len(scores) - passed if scores else 0,
        "pass_percentage": PASS_PERCENTAGE,
        "search": search,
        "status_filter": status_filter,
    })


# ── CSV Export ───────────────────────────────────────────────
@router.get("/exams/{exam_id}/results/csv")
async def export_csv(request: Request, exam_id: str, db: Session = Depends(get_db)):
    faculty = require_faculty(request)

    exam = db.query(Exam).filter(Exam.id == exam_id, Exam.faculty_id == faculty["id"]).first()
    if not exam:
        return RedirectResponse(url="/faculty/dashboard", status_code=303)

    sessions = db.query(StudentSession).filter(
        StudentSession.exam_id == exam_id, StudentSession.is_submitted == True
    ).order_by(StudentSession.student_name).all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["Student Name", "Registration Number", "Score", "Total Marks", "Percentage", "Status", "Submitted At"])

    for s in sessions:
        pct = float(s.percentage) if s.percentage else 0
        status = "Passed" if pct >= PASS_PERCENTAGE else "Failed"
        writer.writerow([
            s.student_name,
            s.registration_number,
            s.score or 0,
            s.total_marks or 0,
            f"{pct:.1f}%",
            status,
            s.submitted_at.isoformat() if s.submitted_at else "",
        ])

    output.seek(0)
    filename = f"results_{exam.title.replace(' ', '_')}.csv"

    return StreamingResponse(
        iter([output.getvalue()]),
        media_type="text/csv",
        headers={"Content-Disposition": f"attachment; filename={filename}"}
    )
