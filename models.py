"""SQLAlchemy ORM models for the Online Technical Examination System."""

import uuid
from datetime import datetime, timezone
from sqlalchemy import (
    Column, String, Integer, Boolean, Text, DateTime, Numeric,
    ForeignKey, UniqueConstraint, CheckConstraint, Index
)
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import relationship
from database import Base


def utcnow():
    return datetime.now(timezone.utc)


def new_uuid():
    return uuid.uuid4()


# ── Faculty ──────────────────────────────────────────────────
class Faculty(Base):
    __tablename__ = "faculty"

    id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    username = Column(String(100), unique=True, nullable=False)
    password_hash = Column(String(255), nullable=False)
    full_name = Column(String(200), nullable=False)
    department = Column(String(200), default="")
    created_at = Column(DateTime(timezone=True), default=utcnow)

    exams = relationship("Exam", back_populates="faculty", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Faculty {self.username}>"


# ── Exams ────────────────────────────────────────────────────
class Exam(Base):
    __tablename__ = "exams"

    id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    faculty_id = Column(UUID(as_uuid=True), ForeignKey("faculty.id", ondelete="CASCADE"), nullable=False)
    title = Column(String(300), nullable=False)
    description = Column(Text, default="")
    duration_minutes = Column(Integer, nullable=False)
    total_marks = Column(Integer, nullable=False)
    start_time = Column(DateTime(timezone=True), nullable=False)
    end_time = Column(DateTime(timezone=True), nullable=False)
    status = Column(String(20), default="draft")
    created_at = Column(DateTime(timezone=True), default=utcnow)
    updated_at = Column(DateTime(timezone=True), default=utcnow, onupdate=utcnow)

    __table_args__ = (
        CheckConstraint("duration_minutes > 0", name="ck_exams_duration"),
        CheckConstraint("total_marks > 0", name="ck_exams_marks"),
        CheckConstraint("status IN ('draft', 'active', 'completed')", name="ck_exams_status"),
        Index("idx_exams_faculty", "faculty_id"),
        Index("idx_exams_status", "status"),
    )

    faculty = relationship("Faculty", back_populates="exams")
    questions = relationship("Question", back_populates="exam", cascade="all, delete-orphan")
    sessions = relationship("StudentSession", back_populates="exam", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Exam {self.title}>"


# ── Questions ────────────────────────────────────────────────
class Question(Base):
    __tablename__ = "questions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    exam_id = Column(UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    question_text = Column(Text, nullable=False)
    option_a = Column(Text, nullable=False)
    option_b = Column(Text, nullable=False)
    option_c = Column(Text, nullable=False)
    option_d = Column(Text, nullable=False)
    correct_option = Column(String(1), nullable=False)
    marks = Column(Integer, nullable=False, default=1)
    order_num = Column(Integer, nullable=False, default=1)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        CheckConstraint("correct_option IN ('A', 'B', 'C', 'D')", name="ck_questions_option"),
        CheckConstraint("marks > 0", name="ck_questions_marks"),
        Index("idx_questions_exam", "exam_id"),
    )

    exam = relationship("Exam", back_populates="questions")
    responses = relationship("StudentResponse", back_populates="question", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Question {self.order_num} of {self.exam_id}>"


# ── Student Sessions ────────────────────────────────────────
class StudentSession(Base):
    __tablename__ = "student_sessions"

    id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    exam_id = Column(UUID(as_uuid=True), ForeignKey("exams.id", ondelete="CASCADE"), nullable=False)
    student_name = Column(String(200), nullable=False)
    registration_number = Column(String(100), nullable=False)
    started_at = Column(DateTime(timezone=True), default=utcnow)
    submitted_at = Column(DateTime(timezone=True), nullable=True)
    score = Column(Integer, nullable=True)
    total_marks = Column(Integer, nullable=True)
    percentage = Column(Numeric(5, 2), nullable=True)
    is_submitted = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("exam_id", "registration_number", name="uq_session_exam_reg"),
        Index("idx_sessions_exam", "exam_id"),
        Index("idx_sessions_reg", "registration_number"),
    )

    exam = relationship("Exam", back_populates="sessions")
    responses = relationship("StudentResponse", back_populates="session", cascade="all, delete-orphan")
    audit_logs = relationship("AuditLog", back_populates="session", cascade="all, delete-orphan")

    def __repr__(self):
        return f"<Session {self.student_name} - {self.registration_number}>"


# ── Student Responses ────────────────────────────────────────
class StudentResponse(Base):
    __tablename__ = "student_responses"

    id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    session_id = Column(UUID(as_uuid=True), ForeignKey("student_sessions.id", ondelete="CASCADE"), nullable=False)
    question_id = Column(UUID(as_uuid=True), ForeignKey("questions.id", ondelete="CASCADE"), nullable=False)
    selected_option = Column(String(1), nullable=True)
    is_correct = Column(Boolean, nullable=True)
    saved_at = Column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        UniqueConstraint("session_id", "question_id", name="uq_response_session_question"),
        CheckConstraint("selected_option IN ('A', 'B', 'C', 'D')", name="ck_response_option"),
        Index("idx_responses_session", "session_id"),
    )

    session = relationship("StudentSession", back_populates="responses")
    question = relationship("Question", back_populates="responses")

    def __repr__(self):
        return f"<Response {self.session_id} Q{self.question_id}>"


# ── Audit Log ────────────────────────────────────────────────
class AuditLog(Base):
    __tablename__ = "audit_log"

    id = Column(UUID(as_uuid=True), primary_key=True, default=new_uuid)
    session_id = Column(UUID(as_uuid=True), ForeignKey("student_sessions.id", ondelete="SET NULL"), nullable=True)
    action = Column(String(100), nullable=False)
    details = Column(Text, default="")
    created_at = Column(DateTime(timezone=True), default=utcnow)

    __table_args__ = (
        Index("idx_audit_session", "session_id"),
    )

    session = relationship("StudentSession", back_populates="audit_logs")

    def __repr__(self):
        return f"<AuditLog {self.action}>"
