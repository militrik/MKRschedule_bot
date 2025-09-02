from __future__ import annotations
from typing import Iterable, Sequence, Tuple
from datetime import datetime, date, timedelta

from sqlalchemy import select, delete, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession

from models import (
    User, Group, Faculty, Chair, Teacher, TimetableEvent, NotificationLog, ZoomLink
)

# ---------- Довідники ----------
async def upsert_faculties(session: AsyncSession, pairs: list[tuple[int, str]]):
    for fid, title in pairs:
        f = await session.get(Faculty, fid)
        if not f:
            f = Faculty(id=fid, title=title)
            session.add(f)

async def upsert_groups(session: AsyncSession, faculty_id: int, course: int, pairs: list[tuple[int, str]]):
    for gid, title in pairs:
        g = await session.get(Group, gid)
        if not g:
            g = Group(id=gid, faculty_id=faculty_id, course=course, title=title)
            session.add(g)
        else:
            if g.title != title:
                g.title = title

async def upsert_chairs(session: AsyncSession, pairs: list[tuple[int, str]]):
    for cid, title in pairs:
        c = await session.get(Chair, cid)
        if not c:
            c = Chair(id=cid, title=title)
            session.add(c)
        else:
            if c.title != title:
                c.title = title

def _short_from_full(full: str) -> str | None:
    parts = full.strip().split()
    if len(parts) >= 2:
        try:
            return f"{parts[0]} {parts[1][0]}.{parts[2][0]}." if len(parts) >= 3 else f"{parts[0]} {parts[1][0]}."
        except Exception:
            return None
    return None

async def upsert_teachers(session: AsyncSession, chair_id: int, pairs: list[tuple[int, str]]):
    for tid, fio in pairs:
        t = await session.get(Teacher, tid)
        if not t:
            t = Teacher(id=tid, chair_id=chair_id, full_name=fio, short_name=_short_from_full(fio))
            session.add(t)
        else:
            changed = False
            if t.full_name != fio:
                t.full_name = fio; changed = True
            if t.chair_id != chair_id:
                t.chair_id = chair_id; changed = True
            if changed:
                t.short_name = t.short_name or _short_from_full(t.full_name)

# ---------- Списки для планувальника ----------
async def distinct_group_ids_in_users(session: AsyncSession) -> set[int]:
    rows = await session.execute(select(User.group_id).where(User.group_id.is_not(None)))
    return set([gid for (gid,) in rows if gid is not None])

async def distinct_teacher_ids_in_users(session: AsyncSession) -> set[int]:
    rows = await session.execute(select(User.teacher_id).where(User.teacher_id.is_not(None)))
    return set([tid for (tid,) in rows if tid is not None])

# ---------- Синхронізація подій ----------
async def sync_events_for_group(session: AsyncSession, group_id: int, new_events: Sequence[TimetableEvent]):
    """
    Простий підхід: видаляємо всі майбутні події групи (від "сьогодні - 1 день"), вставляємо нові.
    """
    cutoff = date.today() - timedelta(days=1)
    await session.execute(
        delete(TimetableEvent).where(
            and_(TimetableEvent.group_id == group_id, TimetableEvent.date >= cutoff)
        )
    )
    for e in new_events:
        e.group_id = group_id
        session.add(e)

async def sync_events_for_teacher(session: AsyncSession, teacher_id: int, new_events: Sequence[TimetableEvent]):
    cutoff = date.today() - timedelta(days=1)
    await session.execute(
        delete(TimetableEvent).where(
            and_(TimetableEvent.teacher_id == teacher_id, TimetableEvent.date >= cutoff)
        )
    )
    for e in new_events:
        e.teacher_id = teacher_id
        session.add(e)

# ---------- Витяг подій для команд ----------
async def events_for_user_day(session: AsyncSession, u: User, target_date: date) -> list[TimetableEvent]:
    if u.role == "teacher" and u.teacher_id:
        q = select(TimetableEvent).where(
            and_(TimetableEvent.teacher_id == u.teacher_id, TimetableEvent.date == target_date)
        ).order_by(TimetableEvent.time_start, TimetableEvent.lesson_number)
    else:
        q = select(TimetableEvent).where(
            and_(TimetableEvent.group_id == u.group_id, TimetableEvent.date == target_date)
        ).order_by(TimetableEvent.time_start, TimetableEvent.lesson_number)
    rows = await session.execute(q)
    return list(rows.scalars())

async def events_for_user_range(session: AsyncSession, u: User, start: date, end: date) -> list[TimetableEvent]:
    if u.role == "teacher" and u.teacher_id:
        q = select(TimetableEvent).where(
            and_(
                TimetableEvent.teacher_id == u.teacher_id,
                TimetableEvent.date >= start,
                TimetableEvent.date <= end
            )
        ).order_by(TimetableEvent.date, TimetableEvent.time_start, TimetableEvent.lesson_number)
    else:
        q = select(TimetableEvent).where(
            and_(
                TimetableEvent.group_id == u.group_id,
                TimetableEvent.date >= start,
                TimetableEvent.date <= end
            )
        ).order_by(TimetableEvent.date, TimetableEvent.time_start, TimetableEvent.lesson_number)
    rows = await session.execute(q)
    return list(rows.scalars())

# ---------- Нагадування ----------
async def upcoming_events_for_user(session: AsyncSession, u: User, now_local_dt, notify_offset_min: int):
    """
    Повертає [(u, event, scheduled_for_dt_local), ...] для конкретного користувача.
    Час початку події (e.date + e.time_start) ∈ (now+offset, now+offset+60с].
    """
    from utils.time import combine_local
    from datetime import timedelta

    window_start = now_local_dt + timedelta(minutes=notify_offset_min)
    window_end = window_start + timedelta(seconds=60)

    if u.role == "teacher" and u.teacher_id:
        q = select(TimetableEvent).where(
            and_(
                TimetableEvent.teacher_id == u.teacher_id,
                TimetableEvent.date >= window_start.date(),
                TimetableEvent.date <= window_end.date(),
            )
        )
    else:
        q = select(TimetableEvent).where(
            and_(
                TimetableEvent.group_id == u.group_id,
                TimetableEvent.date >= window_start.date(),
                TimetableEvent.date <= window_end.date(),
            )
        )
    rows = list((await session.execute(q)).scalars())

    out = []
    for e in rows:
        if not e.time_start:
            continue
        dt_local = combine_local(e.date, e.time_start)
        if window_start < dt_local <= window_end:
            out.append((u, e, dt_local))
    return out

async def has_notification(session: AsyncSession, user_id: int, event_id: int) -> bool:
    q = select(NotificationLog.id).where(
        and_(NotificationLog.user_id == user_id, NotificationLog.event_id == event_id)
    )
    return (await session.scalar(q)) is not None

# ---------- Zoom: список імен для пагінації ----------
async def list_distinct_teachers(session: AsyncSession) -> list[str]:
    names = set()
    rows = await session.execute(
        select(TimetableEvent.teacher_full).where(TimetableEvent.teacher_full.is_not(None))
    )
    for (name,) in rows:
        if name:
            names.add(name)
    rows2 = await session.execute(select(Teacher.full_name))
    for (name,) in rows2:
        if name:
            names.add(name)
    return sorted(names)

# ---------- Zoom: upsert і отримання лінка ----------
async def set_zoom_link(session: AsyncSession, teacher_name: str, url: str):
    """
    Зберігає/оновлює Zoom-лінк для викладача за повним ПІБ.
    Намагатимемось підв'язати до Teacher, якщо знайдемо по full_name.
    """
    name = (teacher_name or "").strip()
    if not name:
        return

    # Спробуємо знайти викладача в довіднику
    t = await session.scalar(select(Teacher).where(Teacher.full_name == name))

    # Шукаємо існуючий запис ZoomLink
    zl = await session.scalar(select(ZoomLink).where(ZoomLink.teacher_name == name))
    if not zl and t:
        zl = await session.scalar(select(ZoomLink).where(ZoomLink.teacher_id == t.id))

    if zl:
        zl.teacher_name = name
        zl.url = url
        if t and zl.teacher_id != t.id:
            zl.teacher_id = t.id
        zl.updated_at = datetime.utcnow()
    else:
        zl = ZoomLink(teacher_id=t.id if t else None, teacher_name=name, url=url, updated_at=datetime.utcnow())
        session.add(zl)

async def zoom_for_event(session: AsyncSession, e: TimetableEvent) -> str | None:
    """
    Повертає Zoom-лінк для події: пріоритет — за повним ПІБ, далі за teacher_id.
    """
    # 1) за повним ПІБ
    if e.teacher_full:
        url = await session.scalar(select(ZoomLink.url).where(ZoomLink.teacher_name == e.teacher_full))
        if url:
            return url
    # 2) за teacher_id (актуально для "режиму викладача")
    if e.teacher_id:
        url = await session.scalar(select(ZoomLink.url).where(ZoomLink.teacher_id == e.teacher_id))
        if url:
            return url
    return None

# ---------- Очищення БД ----------
async def cleanup_old_records(session: AsyncSession, cutoff_event_date: date, cutoff_notif_dt: datetime) -> Tuple[int, int]:
    """
    Видаляє:
      • TimetableEvent із датою < cutoff_event_date
      • NotificationLog із sent_at/ scheduled_for < cutoff_notif_dt
    Повертає (n_events, n_logs).
    """
    # Events
    res1 = await session.execute(
        delete(TimetableEvent).where(TimetableEvent.date < cutoff_event_date)
    )
    n_events = res1.rowcount or 0

    # Logs
    res2 = await session.execute(
        delete(NotificationLog).where(
            or_(
                NotificationLog.sent_at.is_(None) & (NotificationLog.scheduled_for < cutoff_notif_dt),
                NotificationLog.sent_at.is_not(None) & (NotificationLog.sent_at < cutoff_notif_dt),
            )
        )
    )
    n_logs = res2.rowcount or 0

    return n_events, n_logs
