from typing import Iterable, Sequence
from datetime import datetime, date

from sqlalchemy import select, func, delete
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    Faculty,
    Group,
    User,
    TimetableEvent,
    NotificationLog,
    TeacherZoom,
)

# ---------- FACULTIES ----------
async def upsert_faculties(s: AsyncSession, items: list[tuple[int, str]]):
    for fid, title in items:
        row = await s.scalar(select(Faculty).where(Faculty.id == fid))
        if row:
            row.title = title
        else:
            s.add(Faculty(id=fid, title=title))

# ---------- GROUPS ----------
async def upsert_groups(s: AsyncSession, faculty_id: int, course: int, groups: list[tuple[int, str]]):
    for gid, title in groups:
        row = await s.scalar(select(Group).where(Group.id == gid))
        if row:
            row.title = title
            row.faculty_id = faculty_id
            row.course = course
        else:
            s.add(Group(id=gid, faculty_id=faculty_id, course=course, title=title))

# ---------- USERS ----------
async def get_or_create_user(s: AsyncSession, user_id: int) -> User:
    u = await s.scalar(select(User).where(User.user_id == user_id))
    if not u:
        u = User(user_id=user_id)
        s.add(u)
    return u

# ---------- EVENTS (upsert для одиночних подій) ----------
async def upsert_events(s: AsyncSession, events: Iterable[TimetableEvent]):
    for e in events:
        stmt = select(TimetableEvent).where(
            (TimetableEvent.group_id == e.group_id) &
            (TimetableEvent.date == e.date) &
            (TimetableEvent.lesson_number == e.lesson_number) &
            (TimetableEvent.subject_code == e.subject_code) &
            (TimetableEvent.auditory == e.auditory) &
            (TimetableEvent.teacher_short == e.teacher_short)
        )
        exists = await s.scalar(stmt)
        if exists:
            exists.subject_full = e.subject_full
            exists.lesson_type = e.lesson_type
            exists.teacher_full = e.teacher_full
            exists.source_added = e.source_added
            exists.source_hash = e.source_hash
            exists.time_start = e.time_start or exists.time_start
            exists.time_end = e.time_end or exists.time_end
        else:
            s.add(e)

# ---------- EVENTS (повна синхронізація за діапазоном дат) ----------
def _event_key(e: TimetableEvent) -> tuple:
    # Ключ синхронізації (у межах group_id + дати):
    return (e.date, e.lesson_number, e.subject_code, e.auditory, e.teacher_short)

async def sync_events_for_group(
    s: AsyncSession,
    group_id: int,
    new_events: Iterable[TimetableEvent],
) -> int:
    """
    Повна синхронізація за датами, присутніми в new_events:
      - upsert для кожної нової пари;
      - delete старих записів (тільки в межах знайдених дат), яких більше немає.
    Повертає кількість актуальних записів після синку за охоплені дати.
    """
    new_list = list(new_events)
    if not new_list:
        return 0

    dates: set[date] = {e.date for e in new_list if e.date}

    q_old = select(TimetableEvent).where(
        (TimetableEvent.group_id == group_id) &
        (TimetableEvent.date.in_(dates))
    )
    old_rows = list((await s.execute(q_old)).scalars())
    old_map = {_event_key(e): e for e in old_rows}

    new_keys = set()
    for e in new_list:
        key = _event_key(e)
        new_keys.add(key)
        exists = old_map.get(key)
        if exists:
            exists.subject_full = e.subject_full
            exists.lesson_type = e.lesson_type
            exists.teacher_full = e.teacher_full
            exists.source_added = e.source_added
            exists.source_hash = e.source_hash
            exists.time_start = e.time_start or exists.time_start
            exists.time_end = e.time_end or exists.time_end
        else:
            s.add(e)

    to_delete_ids = [old_map[k].id for k in (old_map.keys() - new_keys)]
    if to_delete_ids:
        await s.execute(
            delete(TimetableEvent).where(TimetableEvent.id.in_(to_delete_ids))
        )

    cnt = await s.scalar(
        select(func.count(TimetableEvent.id)).where(
            (TimetableEvent.group_id == group_id) &
            (TimetableEvent.date.in_(dates))
        )
    )
    return int(cnt or 0)

# ---------- REPORTING / HELPERS ----------
async def distinct_group_ids_in_users(s: AsyncSession) -> Sequence[int]:
    rows = await s.execute(select(User.group_id).where(User.group_id.is_not(None)).distinct())
    return [r[0] for r in rows if r[0] is not None]

async def users_for_group(s: AsyncSession, group_id: int) -> Sequence[User]:
    res = await s.execute(select(User).where(User.group_id == group_id))
    return list(res.scalars())

async def upcoming_events_window(s: AsyncSession, kiev_now, offset_min: int):
    """
    Повертає (user, event, scheduled_for_utc) для подій,
    що стартують у вікні [now+offset, now+offset+60s] у TZ Києва.
    """
    from sqlalchemy import select
    from models import TimetableEvent as E, User as U
    from utils.time import combine_local, to_utc

    users = list((await s.execute(select(U).where(U.group_id.is_not(None)))).scalars())
    results = []
    for u in users:
        events = list((await s.execute(select(E).where(E.group_id == u.group_id))).scalars())
        for e in events:
            if not e.time_start:
                continue
            dt_local = combine_local(e.date, e.time_start)
            delta = (dt_local - kiev_now).total_seconds()
            if (offset_min * 60) <= delta < (offset_min * 60 + 60):
                results.append((u, e, to_utc(dt_local)))
    return results

async def has_notification(s: AsyncSession, user_id: int, event_id: int) -> bool:
    return await s.scalar(
        select(func.count(NotificationLog.id)).where(
            (NotificationLog.user_id == user_id) & (NotificationLog.event_id == event_id)
        )
    ) > 0

# ---------- TEACHER ZOOM ----------
async def list_distinct_teachers(s: AsyncSession) -> list[str]:
    """Унікальні імена викладачів із подій (повні та скорочені)."""
    res1 = await s.execute(
        select(TimetableEvent.teacher_full)
        .where(TimetableEvent.teacher_full.is_not(None))
        .distinct()
    )
    res2 = await s.execute(
        select(TimetableEvent.teacher_short)
        .where(TimetableEvent.teacher_short.is_not(None))
        .distinct()
    )
    names = {r[0] for r in res1 if r[0]} | {r[0] for r in res2 if r[0]}
    return sorted(names, key=lambda x: x.lower())

async def set_zoom_link(s: AsyncSession, teacher_name: str, url: str):
    """Створити/оновити Zoom для викладача (за ключем-іменем)."""
    row = await s.get(TeacherZoom, teacher_name)
    now = datetime.utcnow()
    if row:
        row.zoom_url = url
        row.updated_at = now
    else:
        s.add(TeacherZoom(teacher_name=teacher_name, zoom_url=url, updated_at=now))

async def get_zoom_link(s: AsyncSession, teacher_name: str) -> str | None:
    row = await s.get(TeacherZoom, teacher_name)
    return row.zoom_url if row else None

async def zoom_for_event(s: AsyncSession, e: TimetableEvent) -> str | None:
    """Підібрати Zoom за full або short ім'ям викладача."""
    for name in (e.teacher_full, e.teacher_short):
        if not name:
            continue
        z = await s.get(TeacherZoom, name)
        if z:
            return z.zoom_url
    return None

# ---------- CLEANUP ----------
async def cleanup_old_records(
    s: AsyncSession,
    cutoff_event_date: date,
    cutoff_notif_dt: datetime,
) -> dict:
    """
    Видаляє:
      1) NotificationLog, які старші за cutoff_notif_dt;
      2) NotificationLog, пов'язані з подіями старшими за cutoff_event_date;
      3) TimetableEvent зі ️датою < cutoff_event_date.
    Повертає dict з кількостями видалених рядків.
    """
    # Спочатку — логи, щоб не ламати FK на events
    # 1) старі за часом
    res1 = await s.execute(
        delete(NotificationLog).where(NotificationLog.scheduled_for < cutoff_notif_dt)
    )
    deleted_logs_time = res1.rowcount or 0

    # 2) логи, чиї події старші за cutoff_event_date
    subq_old_events = select(TimetableEvent.id).where(TimetableEvent.date < cutoff_event_date)
    res2 = await s.execute(
        delete(NotificationLog).where(NotificationLog.event_id.in_(subq_old_events))
    )
    deleted_logs_by_events = res2.rowcount or 0

    # 3) самі події
    res3 = await s.execute(
        delete(TimetableEvent).where(TimetableEvent.date < cutoff_event_date)
    )
    deleted_events = res3.rowcount or 0

    return {
        "deleted_logs_time": int(deleted_logs_time),
        "deleted_logs_by_events": int(deleted_logs_by_events),
        "deleted_events": int(deleted_events),
    }
