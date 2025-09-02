from datetime import date, time, datetime
from sqlalchemy import ForeignKey, UniqueConstraint, Index
from sqlalchemy.orm import Mapped, mapped_column, relationship
from db import Base

class Faculty(Base):
    __tablename__ = "faculties"
    id: Mapped[int] = mapped_column(primary_key=True)  # з сайту
    title: Mapped[str] = mapped_column(unique=True)
    updated_at: Mapped[datetime | None]

class Group(Base):
    __tablename__ = "groups"
    id: Mapped[int] = mapped_column(primary_key=True)  # value селекта
    faculty_id: Mapped[int] = mapped_column(ForeignKey("faculties.id"))
    course: Mapped[int]
    title: Mapped[str]
    last_schedule_hash: Mapped[str | None]
    last_checked_at: Mapped[datetime | None]

    faculty = relationship("Faculty")

    __table_args__ = (
        UniqueConstraint("faculty_id", "course", "title", name="uq_group_fac_course_title"),
        Index("ix_group_fac_course", "faculty_id", "course"),
    )

class User(Base):
    __tablename__ = "users"
    user_id: Mapped[int] = mapped_column(primary_key=True)  # Telegram ID
    faculty_id: Mapped[int | None] = mapped_column(ForeignKey("faculties.id"))
    course: Mapped[int | None]
    group_id: Mapped[int | None] = mapped_column(ForeignKey("groups.id"))
    notify_offset_min: Mapped[int] = mapped_column(default=5)
    lang: Mapped[str] = mapped_column(default="uk")
    created_at: Mapped[datetime | None]
    updated_at: Mapped[datetime | None]

    faculty = relationship("Faculty")
    group = relationship("Group")

    __table_args__ = (Index("ix_users_group", "group_id"),)

class TimetableEvent(Base):
    __tablename__ = "timetable_events"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    date: Mapped[date]
    weekday: Mapped[str | None]
    lesson_number: Mapped[int]
    time_start: Mapped[time | None]
    time_end: Mapped[time | None]
    subject_code: Mapped[str | None]
    subject_full: Mapped[str | None]
    lesson_type: Mapped[str | None]
    auditory: Mapped[str | None]
    teacher_short: Mapped[str | None]
    teacher_full: Mapped[str | None]
    source_added: Mapped[date | None]
    source_url: Mapped[str | None]
    source_hash: Mapped[str | None]
    raw_html: Mapped[str | None]

    __table_args__ = (
        UniqueConstraint(
            "group_id", "date", "lesson_number",
            "subject_code", "auditory", "teacher_short",
            name="uq_event_dedup"
        ),
        Index("ix_events_group_date", "group_id", "date"),
        Index("ix_events_date_time", "date", "time_start"),
    )

class NotificationLog(Base):
    __tablename__ = "notifications_log"
    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.user_id"))
    group_id: Mapped[int] = mapped_column(ForeignKey("groups.id"))
    event_id: Mapped[int] = mapped_column(ForeignKey("timetable_events.id"))
    scheduled_for: Mapped[datetime]
    sent_at: Mapped[datetime | None]
    status: Mapped[str]  # queued/sent/failed/skipped
    error: Mapped[str | None]

    __table_args__ = (
        UniqueConstraint("user_id", "event_id", name="uq_notif_once"),
        Index("ix_notif_when", "scheduled_for"),
    )

class TeacherZoom(Base):
    __tablename__ = "teacher_zoom"
    teacher_name: Mapped[str] = mapped_column(primary_key=True)  # ключ = як у розкладі (повне або скорочене ім'я)
    zoom_url: Mapped[str]
    updated_at: Mapped[datetime | None]
