from __future__ import annotations
from typing import Iterable, Sequence, Tuple, Dict, Any
from datetime import datetime, date, timedelta, time

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
        else:
            if f.title != title:
                f.title = title

async def upsert_groups(session: AsyncSession, faculty_id: int, course: int, pairs: list[tuple[int, str]]):
    for gid, title in pairs:
        g = await session.get(Group, gid)
        if not g:
            g = Group(id=gid, faculty_id=faculty_id, course=course, title=title)
            session.add(g)
        else:
            changed = False
            if g.title != title:
                g.title = title; changed = True
            if g.faculty_id != faculty_id:
                g.faculty_id = faculty_id; changed = True
            if g.course != course:
                g.course = course; changed = True

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
    parts = (full or "").strip().split()
    if len(parts) >= 2:
        try:
            ini2 = f"{parts[1][0]}."
            ini3 = f"{parts[2][0]}." if len(parts) >= 3 else ""
            return f"{parts[0]} {ini2}{ini3}".strip()
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
            if changed and not t.short_name:
                t.short_name = _short_from_full(t.full_name)

# ---------- Ключ, відбиток і порівняння події ----------
def _event_natural_key(e: TimetableEvent) -> tuple:
    """
    Стабільний ключ тайм-слоту:
    • перевага — (date, lesson_number); якщо № пари відсутній — (date, time_start)
    Це дозволяє «оновлювати» запис замість видаляти-вставляти.
    """
    ln = getattr(e, "lesson_number", None)
    ts: time | None = getattr(e, "time_start", None)
    if ln is not None:
        return (e.date, ("ln", int(ln)))
    if ts is not None:
        return (e.date, ("ts", ts.hour, ts.minute))
    # крайній випадок — тільки дата; але на таких записах оновлення не гарантується
    return (e.date, ("misc", (e.subject_code or "").strip(), (e.subject_full or "").strip()))

_FINGER_FIELDS = (
    "time_start", "time_end", "lesson_number",
    "subject_code", "subject_full", "lesson_type",
    "auditory",
    "teacher_full", "teacher_short",
    "groups_text",
)

def _event_fingerprint_dict(values: Dict[str, Any]) -> tuple:
    out = []
    for f in _FINGER_FIELDS:
        v = values.get(f)
        if isinstance(v, str):
            v = v.strip()
        out.append(v)
    return tuple(out)

def _event_to_values(e: TimetableEvent) -> Dict[str, Any]:
    return {f: getattr(e, f) for f in _FINGER_FIELDS}

# ---------- Списки для планувальника ----------
async def distinct_group_ids_in_users(session: AsyncSession) -> set[int]:
    rows = await session.execute(select(User.group_id).where(User.group_id.is_not(None)))
    return set([gid for (gid,) in rows if gid is not None])

async def distinct_teacher_ids_in_users(session: AsyncSession) -> set[int]:
    rows = await session.execute(select(User.teacher_id).where(User.teacher_id.is_not(None)))
    return set([tid for (tid,) in rows if tid is not None])

# ---------- Диференційна синхронізація ----------
async def _load_existing_for_group(session: AsyncSession, group_id: int, cutoff: date) -> Dict[tuple, TimetableEvent]:
    rows = list((await session.execute(
        select(TimetableEvent).where(
            and_(TimetableEvent.group_id == group_id, TimetableEvent.date >= cutoff)
        )
    )).scalars())
    return { _event_natural_key(e): e for e in rows }

async def _load_existing_for_teacher(session: AsyncSession, teacher_id: int, cutoff: date) -> Dict[tuple, TimetableEvent]:
    rows = list((await session.execute(
        select(TimetableEvent).where(
            and_(TimetableEvent.teacher_id == teacher_id, TimetableEvent.date >= cutoff)
        )
    )).scalars())
    return { _event_natural_key(e): e for e in rows }

async def sync_events_for_group(session: AsyncSession, group_id: int, new_events: Sequence[TimetableEvent]):
    """
    Диференційна синхронізація:
      • додаємо нові тайм-слоти,
      • видаляємо зниклі,
      • оновлюємо змінені поля без зміни ID події.
    """
    cutoff = date.today() - timedelta(days=1)
    existing_map = await _load_existing_for_group(session, group_id, cutoff)

    # Підготувати нові
    new_map: Dict[tuple, TimetableEvent] = {}
    for e in new_events:
        e.group_id = group_id
        new_map[_event_natural_key(e)] = e

    # Видалити ті, яких більше нема
    for key, old in list(existing_map.items()):
        if key not in new_map:
            await session.delete(old)

    # Додати/оновити
    for key, new_e in new_map.items():
        old = existing_map.get(key)
        if not old:
            session.add(new_e)
            continue
        # порівняти відбиток
        old_fp = _event_fingerprint_dict(_event_to_values(old))
        new_fp = _event_fingerprint_dict(_event_to_values(new_e))
        if old_fp != new_fp:
            vals = _event_to_values(new_e)
            for f, v in vals.items():
                setattr(old, f, v)
            # якщо у моделі є updated_at — оновимо
            if hasattr(old, "updated_at"):
                setattr(old, "updated_at", datetime.utcnow())

async def sync_events_for_teacher(session: AsyncSession, teacher_id: int, new_events: Sequence[TimetableEvent]):
    cutoff = date.today() - timedelta(days=1)
    existing_map = await _load_existing_for_teacher(session, teacher_id, cutoff)

    new_map: Dict[tuple, TimetableEvent] = {}
    for e in new_events:
        e.teacher_id = teacher_id
        new_map[_event_natural_key(e)] = e

    for key, old in list(existing_map.items()):
        if key not in new_map:
            await session.delete(old)

    for key, new_e in new_map.items():
        old = existing_map.get(key)
        if not old:
            session.add(new_e)
            continue
        old_fp = _event_fingerprint_dict(_event_to_values(old))
        new_fp = _event_fingerprint_dict(_event_to_values(new_e))
        if old_fp != new_fp:
            vals = _event_to_values(new_e)
            for f, v in vals.items():
                setattr(old, f, v)
            if hasattr(old, "updated_at"):
                setattr(old, "updated_at", datetime.utcnow())

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
async def upcoming_events_for_user_between(session: AsyncSession, u: User, window_start_local, window_end_local):
    """
    Повертає [(u, event, scheduled_for_dt_local), ...] у вікні [window_start_local, window_end_local].
    """
    from utils.time import combine_local

    if u.role == "teacher" and u.teacher_id:
        q = select(TimetableEvent).where(
            and_(
                TimetableEvent.teacher_id == u.teacher_id,
                TimetableEvent.date >= window_start_local.date(),
                TimetableEvent.date <= window_end_local.date(),
            )
        )
    else:
        q = select(TimetableEvent).where(
            and_(
                TimetableEvent.group_id == u.group_id,
                TimetableEvent.date >= window_start_local.date(),
                TimetableEvent.date <= window_end_local.date(),
            )
        )
    rows = list((await session.execute(q)).scalars())

    out = []
    for e in rows:
        if not e.time_start:
            continue
        dt_local = combine_local(e.date, e.time_start)
        if window_start_local < dt_local <= window_end_local:
            out.append((u, e, dt_local))
    return out

async def has_notification_around(session: AsyncSession, user_id: int, event_id: int, scheduled_for_dt, tolerance_seconds: int = 180) -> bool:
    """
    Чи вже надсилали сповіщення для ЦЬОГО event_id приблизно на цей самий час?
    Використовуємо поле NotificationLog.scheduled_for.
    """
    lo = scheduled_for_dt - timedelta(seconds=tolerance_seconds)
    hi = scheduled_for_dt + timedelta(seconds=tolerance_seconds)
    q = select(NotificationLog.id).where(
        and_(
            NotificationLog.user_id == user_id,
            NotificationLog.event_id == event_id,
            NotificationLog.scheduled_for >= lo,
            NotificationLog.scheduled_for <= hi,
        )
    )
    return (await session.scalar(q)) is not None

# ---------- Zoom ----------
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

async def set_zoom_link(session: AsyncSession, teacher_name: str, url: str):
    name = (teacher_name or "").strip()
    if not name:
        return
    t = await session.scalar(select(Teacher).where(Teacher.full_name == name))
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
    if e.teacher_full:
        url = await session.scalar(select(ZoomLink.url).where(ZoomLink.teacher_name == e.teacher_full))
        if url:
            return url
    if e.teacher_id:
        url = await session.scalar(select(ZoomLink.url).where(ZoomLink.teacher_id == e.teacher_id))
        if url:
            return url
    return None

# ---------- Очищення БД ----------
async def cleanup_old_records(session: AsyncSession, cutoff_event_date: date, cutoff_notif_dt: datetime) -> Tuple[int, int]:
    res1 = await session.execute(
        delete(TimetableEvent).where(TimetableEvent.date < cutoff_event_date)
    )
    n_events = res1.rowcount or 0

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
