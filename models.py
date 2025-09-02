from __future__ import annotations
from datetime import datetime, date, time
from typing import Optional

from sqlalchemy import (
    Column, Integer, String, Date, Time, DateTime, Text, ForeignKey, Index
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


# ---------- Довідники ----------
class Faculty(Base):
    __tablename__ = "faculties"
    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)


class Chair(Base):
    __tablename__ = "chairs"
    id = Column(Integer, primary_key=True)
    title = Column(String(255), nullable=False)

    teachers = relationship("Teacher", back_populates="chair")


class Group(Base):
    __tablename__ = "groups"
    id = Column(Integer, primary_key=True)
    faculty_id = Column(Integer, ForeignKey("faculties.id"), nullable=True)
    course = Column(Integer, nullable=True)
    title = Column(String(255), nullable=False)

    users = relationship("User", back_populates="group")


class Teacher(Base):
    __tablename__ = "teachers"
    id = Column(Integer, primary_key=True)
    full_name = Column(String(255), nullable=False)
    short_name = Column(String(255), nullable=True)
    chair_id = Column(Integer, ForeignKey("chairs.id"), nullable=True)

    chair = relationship("Chair", back_populates="teachers")


# ---------- Користувач ----------
class User(Base):
    __tablename__ = "users"
    user_id = Column(Integer, primary_key=True)  # telegram id
    role = Column(String(16), nullable=False, default="student")  # 'student' | 'teacher'

    # student:
    faculty_id = Column(Integer, ForeignKey("faculties.id"), nullable=True)
    course = Column(Integer, nullable=True)
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)

    # teacher:
    chair_id = Column(Integer, ForeignKey("chairs.id"), nullable=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)

    notify_offset_min = Column(Integer, nullable=False, default=5)

    group = relationship("Group", back_populates="users")


# ---------- Розклад ----------
class TimetableEvent(Base):
    __tablename__ = "timetable_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    # Прив'язка для студентського режиму
    group_id = Column(Integer, ForeignKey("groups.id"), nullable=True)
    # Прив'язка для викладацького режиму
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)

    date = Column(Date, nullable=False)
    weekday = Column(Integer, nullable=True)
    lesson_number = Column(Integer, nullable=True)

    time_start = Column(Time, nullable=True)
    time_end = Column(Time, nullable=True)

    subject_code = Column(String(64), nullable=True)
    subject_full = Column(Text, nullable=True)
    lesson_type = Column(String(64), nullable=True)

    auditory = Column(String(128), nullable=True)

    teacher_short = Column(String(255), nullable=True)
    teacher_full = Column(String(255), nullable=True)

    # Для викладача: перелік груп у клітинці
    groups_text = Column(Text, nullable=True)

    source_added = Column(Date, nullable=True)
    source_url = Column(Text, nullable=True)
    source_hash = Column(String(64), nullable=True)
    raw_html = Column(Text, nullable=True)


# Індекси для швидкого пошуку/ідемпотентності
Index(
    "ix_events_group_day",
    TimetableEvent.group_id, TimetableEvent.date, TimetableEvent.time_start, TimetableEvent.lesson_number
)
Index(
    "ix_events_teacher_day",
    TimetableEvent.teacher_id, TimetableEvent.date, TimetableEvent.time_start, TimetableEvent.lesson_number
)


# ---------- Zoom-лінки ----------
class ZoomLink(Base):
    __tablename__ = "zoom_links"
    id = Column(Integer, primary_key=True, autoincrement=True)
    teacher_id = Column(Integer, ForeignKey("teachers.id"), nullable=True)
    teacher_name = Column(String(255), nullable=False, unique=True)  # повний ПІБ як ключ
    url = Column(Text, nullable=False)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


# ---------- Логи розсилки ----------
class NotificationLog(Base):
    __tablename__ = "notification_log"
    id = Column(Integer, primary_key=True, autoincrement=True)

    user_id = Column(Integer, nullable=False)
    group_id = Column(Integer, nullable=True)
    event_id = Column(Integer, ForeignKey("timetable_events.id"), nullable=False)

    scheduled_for = Column(DateTime, nullable=False)
    sent_at = Column(DateTime, nullable=True)
    status = Column(String(16), nullable=False)
    error = Column(Text, nullable=True)
