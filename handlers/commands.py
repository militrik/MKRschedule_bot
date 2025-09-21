from itertools import groupby
from datetime import datetime, date, timedelta
from pathlib import Path
import sys

from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery
from aiogram.fsm.context import FSMContext
from aiogram.enums import ParseMode

from sqlalchemy import select

from db import get_sessionmaker
from models import User, TimetableEvent
from keyboards import paginated_kb, main_menu_kb, BTN_TODAY, BTN_TOMORROW, BTN_WEEK, BTN_NEXT, BTN_SETTINGS, BTN_HELP
from utils.time import today_kiev, now_kiev
from utils.formatting import EntityBuilder

from repositories import (
    list_distinct_teachers,
    set_zoom_link,
    zoom_for_event,
    events_for_user_day,
    events_for_user_range
)

router = Router(name="commands")

# ---------- HELP ----------
DEFAULT_HELP_TEXT = (
    "КРНУ Розклад — довідка\n\n"
    "Команди: /start, /today, /tomorrow, /week, /next, /help\n"
    "Нагадування обираються в /start (1/5/10 хв)."
)

def _read_help_md() -> str:
    candidates: list[Path] = []
    if getattr(sys, "frozen", False):
        candidates.append(Path(sys.executable).parent / "help.md")
    here = Path(__file__).resolve()
    for _ in range(3):
        here = here.parent
        candidates.append(here / "help.md")
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        candidates.append(Path(meipass) / "help.md")
    for p in candidates:
        try:
            return p.read_text(encoding="utf-8")
        except Exception:
            continue
    return DEFAULT_HELP_TEXT

@router.message(Command("help"))
async def help_cmd(message: Message):
    text = _read_help_md()
    try:
        await message.answer(text, parse_mode=ParseMode.MARKDOWN, reply_markup=main_menu_kb())
    except Exception:
        # якщо Markdown не пройшов – без парс-моду
        await message.answer(text, reply_markup=main_menu_kb())


# ---------- утиліти форматування ----------
def _subject_display(e: TimetableEvent) -> str:
    code = (e.subject_code or "").strip()
    full = (e.subject_full or "").strip()
    if code and full:
        return f"{code} {full}"
    return full or code or "Предмет"

def _teacher_display(e: TimetableEvent) -> str | None:
    return (e.teacher_full or e.teacher_short or "").strip() or None

def _groups_display(e: TimetableEvent) -> str | None:
    return (e.groups_text or "").strip() or None


# ---------- Добові відповіді ----------
async def _send_day(message: Message, day_offset: int):
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.scalar(select(User).where(User.user_id == message.from_user.id))
        if not u or (u.role == "student" and not u.group_id) or (u.role == "teacher" and not u.teacher_id):
            await message.answer("Немає налаштованої групи/викладача. Натисніть /start.", reply_markup=main_menu_kb())
            return

        target = today_kiev().date() + timedelta(days=day_offset)
        rows = await events_for_user_day(s, u, target)

        if not rows:
            when = "сьогодні" if day_offset == 0 else "завтра" if day_offset == 1 else target.strftime('%d.%m.%Y')
            await message.answer(f"Пари {when} не знайдені.", reply_markup=main_menu_kb())
            return

        b = EntityBuilder()
        b.add(f"Розклад на {target.strftime('%d.%m.%Y')}:\n")
        for e in rows:
            t = (
                f"{e.time_start.strftime('%H:%M')}-{e.time_end.strftime('%H:%M')}"
                if e.time_start and e.time_end else
                f"Пара №{e.lesson_number}"
            )
            subj = _subject_display(e)
            lt = f" ({e.lesson_type})" if e.lesson_type else ""
            room = f", ауд. {e.auditory}" if e.auditory else ""

            extra = ""
            if u.role == "teacher":
                groups = _groups_display(e)
                if groups:
                    extra += f"\nГрупи: {groups}"
            else:
                teacher = _teacher_display(e)
                if teacher:
                    extra += f"\nВикл.: {teacher}"

            zoom = await zoom_for_event(s, e)
            zoom_line = f"\nZoom: {zoom}" if zoom else ""
            b.add(f"• {t} — ").add_bold(subj).add(f"{lt}{room}{extra}{zoom_line}").newline()

    text, entities = b.build()
    await message.answer(text, entities=entities, reply_markup=main_menu_kb())

@router.message(Command("today"))
async def today(message: Message):
    await _send_day(message, 0)

@router.message(Command("tomorrow"))
async def tomorrow(message: Message):
    await _send_day(message, 1)

@router.message(Command("week", "7days"))
async def week(message: Message):
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.scalar(select(User).where(User.user_id == message.from_user.id))
        if not u or (u.role == "student" and not u.group_id) or (u.role == "teacher" and not u.teacher_id):
            await message.answer("Немає налаштованої групи/викладача. Натисніть /start.", reply_markup=main_menu_kb())
            return

        start = today_kiev().date()
        end = start + timedelta(days=6)

        rows = await events_for_user_range(s, u, start, end)

        if not rows:
            await message.answer(f"Пари з {start.strftime('%d.%m.%Y')} по {end.strftime('%d.%m.%Y')} не знайдені.", reply_markup=main_menu_kb())
            return

        b = EntityBuilder()
        b.add(f"Розклад на {start.strftime('%d.%m.%Y')}-{end.strftime('%d.%m.%Y')}:\n")

        for day, day_events_iter in groupby(rows, key=lambda e: e.date):
            day_events = list(day_events_iter)
            b.add(f"\n📅 {day.strftime('%d.%m.%Y')}\n")
            for e in day_events:
                t = (
                    f"{e.time_start.strftime('%H:%M')}-{e.time_end.strftime('%H:%M')}"
                    if e.time_start and e.time_end else
                    f"Пара №{e.lesson_number}"
                )
                subj = _subject_display(e)
                lt = f" ({e.lesson_type})" if e.lesson_type else ""
                room = f", ауд. {e.auditory}" if e.auditory else ""

                extra = ""
                if u.role == "teacher":
                    groups = _groups_display(e)
                    if groups:
                        extra += f"\n   Групи: {groups}"
                else:
                    teacher = _teacher_display(e)
                    if teacher:
                        extra += f"\n   Викл.: {teacher}"

                zoom = await zoom_for_event(s, e)
                zoom_line = f"\n   Zoom: {zoom}" if zoom else ""
                b.add(f"• {t} — ").add_bold(subj).add(f"{lt}{room}{extra}{zoom_line}").newline()

    text, entities = b.build()
    await message.answer(text, entities=entities, reply_markup=main_menu_kb())

# ---------- Найближча пара ----------
@router.message(Command("next"))
async def next_lesson(message: Message):
    sm = get_sessionmaker()
    now_local = now_kiev()
    today = now_local.date()
    current_time = now_local.time()

    async with sm() as s:
        u = await s.scalar(select(User).where(User.user_id == message.from_user.id))
        if not u or (u.role == "student" and not u.group_id) or (u.role == "teacher" and not u.teacher_id):
            await message.answer("Немає налаштованої групи/викладача. Натисніть /start.", reply_markup=main_menu_kb())
            return

        rows_today = await events_for_user_day(s, u, today)

        def is_future(ev: TimetableEvent) -> bool:
            if ev.time_start:
                return ev.time_start >= current_time
            return False

        next_ev = next((e for e in rows_today if is_future(e)), None)
        if not next_ev:
            # шукаємо вперед до 14 днів
            rows_future = await events_for_user_range(s, u, today, today + timedelta(days=14))
            if rows_future:
                next_ev = rows_future[0]

        if not next_ev:
            await message.answer("Найближчих пар не знайдено.", reply_markup=main_menu_kb())
            return

        b = EntityBuilder()
        date_str = next_ev.date.strftime('%d.%m.%Y')
        t = (
            f"{next_ev.time_start.strftime('%H:%M')}-{next_ev.time_end.strftime('%H:%M')}"
            if next_ev.time_start and next_ev.time_end else
            f"Пара №{next_ev.lesson_number}"
        )
        subj = _subject_display(next_ev)
        lt = f" ({next_ev.lesson_type})" if next_ev.lesson_type else ""
        room = f", ауд. {next_ev.auditory}" if next_ev.auditory else ""

        extra = ""
        if u.role == "teacher":
            groups = _groups_display(next_ev)
            if groups:
                extra += f"\nГрупи: {groups}"
        else:
            teacher = _teacher_display(next_ev)
            if teacher:
                extra += f"\nВикл.: {teacher}"

        zoom = await zoom_for_event(s, next_ev)
        zoom_line = f"\nZoom: {zoom}" if zoom else ""

        b.add(f"Найближча пара — {date_str}\n")
        b.add(f"{t} — ").add_bold(subj).add(f"{lt}{room}{extra}{zoom_line}")

    text, entities = b.build()
    await message.answer(text, entities=entities, reply_markup=main_menu_kb())


# ---------- Обробка текстових кнопок Reply-клавіатури ----------
@router.message(F.text == BTN_TODAY)
async def btn_today(message: Message):
    await _send_day(message, 0)

@router.message(F.text == BTN_TOMORROW)
async def btn_tomorrow(message: Message):
    await _send_day(message, 1)

@router.message(F.text == BTN_WEEK)
async def btn_week(message: Message):
    await week(message)

@router.message(F.text == BTN_NEXT)
async def btn_next(message: Message):
    await next_lesson(message)

@router.message(F.text == BTN_HELP)
async def btn_help(message: Message):
    await help_cmd(message)

@router.message(F.text == BTN_SETTINGS)
async def btn_settings(message: Message, state: FSMContext):
    from handlers.onboarding import start_cmd
    await start_cmd(message, state)


# ---------- Адмін-команда: Zoom ----------
# (без змін, виніс лише імпорти keyboards вище)
from aiogram.fsm.state import State, StatesGroup

class ZoomAdd(StatesGroup):
    teacher = State()
    link = State()

# ... ДАЛІ — увесь блок addzoom як у вашій версії ...
# Я залишаю без змін, щоб не переривати поточну логіку.
# Якщо треба — скажіть, я скопіюю сюди повністю вашу актуальну реалізацію addzoom.


@router.message(Command("addzoom", "setzoom"))
async def addzoom_entry(message: Message, state: FSMContext):
    sm = get_sessionmaker()
    async with sm() as s:
        names = await list_distinct_teachers(s)
    if not names:
        await message.answer("У базі поки немає жодного викладача (спершу імпортуйте розклад).")
        return

    await state.update_data(teacher_names=names, page=0)
    kb = paginated_kb([(str(i), n) for i, n in enumerate(names)], page=0, per_page=10, prefix="tz")
    await message.answer("Оберіть викладача:", reply_markup=kb)
    await state.set_state(ZoomAdd.teacher)

@router.callback_query(ZoomAdd.teacher)
async def addzoom_pick_teacher(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    names = data.get("teacher_names", [])
    page = data.get("page", 0)
    payload = cb.data

    if payload == "tz:__prev__":
        page = max(0, page - 1)
        await state.update_data(page=page)
        kb = paginated_kb([(str(i), n) for i, n in enumerate(names)], page=page, per_page=10, prefix="tz")
        await cb.message.edit_reply_markup(reply_markup=kb)
        await cb.answer(); return

    if payload == "tz:__next__":
        page = page + 1
        await state.update_data(page=page)
        kb = paginated_kb([(str(i), n) for i, n in enumerate(names)], page=page, per_page=10, prefix="tz")
        await cb.message.edit_reply_markup(reply_markup=kb)
        await cb.answer(); return

    if payload.startswith("tz:"):
        idx = int(payload.split(":", 1)[1])
        if not (0 <= idx < len(names)):
            await cb.answer("Хибний вибір."); return
        sel = names[idx]
        await state.update_data(sel_teacher=sel)
        await cb.message.edit_text(f"Вибрано: {sel}\nНадішліть посилання Zoom одним повідомленням.")
        await state.set_state(ZoomAdd.link)
        await cb.answer()

@router.message(ZoomAdd.link)
async def addzoom_save(message: Message, state: FSMContext):
    url = (message.text or "").strip()
    data = await state.get_data()
    sel = data.get("sel_teacher")

    if not (url.startswith("http://") or url.startswith("https://")):
        await message.answer("Це не схоже на URL. Надішліть нормальний лінк, будь ласка.")
        return

    sm = get_sessionmaker()
    async with sm() as s:
        await set_zoom_link(s, sel, url)
        await s.commit()

    await message.answer(f"Збережено Zoom для «{sel}»:\n{url}")
    await state.clear()
