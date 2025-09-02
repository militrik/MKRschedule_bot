from aiogram import Router, F
from aiogram.types import Message, CallbackQuery
from aiogram.filters import CommandStart
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from sqlalchemy import select

from db import get_sessionmaker
from repositories import (
    get_or_create_user,
    upsert_faculties,
    upsert_groups,
    sync_events_for_group,
)
from parsing.client import SourceClient
from parsing.extractors import parse_faculties, parse_courses, parse_groups, parse_timetable
from keyboards import simple_list_kb, paginated_kb
from models import TimetableEvent, User
from config import Config

router = Router(name="onboarding")

class Onb(StatesGroup):
    faculty = State()
    course = State()
    group = State()
    notify = State()   # вибір хвилин для нагадування

@router.message(CommandStart())
async def start(message: Message, state: FSMContext):
    await state.clear()
    await message.answer("Привіт! Оберіть свій факультет…")
    # тягнемо селект факультетів
    cfg = Config.load()
    async with SourceClient(cfg) as sc:
        html = await sc.get_start_page()
    faculties = parse_faculties(html)

    # зберігаємо в БД
    sm = get_sessionmaker()
    async with sm() as s:
        await upsert_faculties(s, faculties)
        await s.commit()

    kb = simple_list_kb([(f"fac:{fid}", title) for fid, title in faculties])
    await message.answer("Факультет:", reply_markup=kb)
    await state.set_state(Onb.faculty)

@router.callback_query(Onb.faculty, F.data.startswith("fac:"))
async def pick_faculty(cb: CallbackQuery, state: FSMContext):
    fid = int(cb.data.split(":", 1)[1])
    await state.update_data(faculty_id=fid)
    await cb.message.edit_text("Оберіть курс:")
    # курси
    cfg = Config.load()
    async with SourceClient(cfg) as sc:
        html = await sc.post_filter(faculty_id=fid, course=None, group_id=None)
    courses = parse_courses(html) or [1, 2, 3, 4]
    kb = simple_list_kb([(f"course:{c}", str(c)) for c in courses])
    await cb.message.edit_reply_markup(reply_markup=kb)
    await state.set_state(Onb.course)
    await cb.answer()

@router.callback_query(Onb.course, F.data.startswith("course:"))
async def pick_course(cb: CallbackQuery, state: FSMContext):
    course = int(cb.data.split(":", 1)[1])
    data = await state.get_data()
    fid = data["faculty_id"]
    await state.update_data(course=course)
    await cb.message.edit_text("Оберіть групу (може бути багато, прокрутіть сторінки кнопками):")

    # тягнемо групи
    cfg = Config.load()
    async with SourceClient(cfg) as sc:
        html = await sc.post_filter(faculty_id=fid, course=course, group_id=None)
    groups = parse_groups(html)

    # збережемо групи в БД
    sm = get_sessionmaker()
    async with sm() as s:
        await upsert_groups(s, fid, course, groups)
        await s.commit()

    # пагінація
    await state.update_data(groups=groups, page=0)
    kb = paginated_kb([(str(id), title) for id, title in groups], page=0, per_page=10, prefix="grp")
    await cb.message.edit_reply_markup(reply_markup=kb)
    await state.set_state(Onb.group)
    await cb.answer()

@router.callback_query(Onb.group)
async def pick_group(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    groups = data.get("groups", [])
    page = data.get("page", 0)
    payload = cb.data

    if payload == "grp:__prev__":
        page = max(0, page - 1)
        await state.update_data(page=page)
        kb = paginated_kb([(str(id), title) for id, title in groups], page=page, per_page=10, prefix="grp")
        await cb.message.edit_reply_markup(reply_markup=kb)
        await cb.answer()
        return

    if payload == "grp:__next__":
        page = page + 1
        await state.update_data(page=page)
        kb = paginated_kb([(str(id), title) for id, title in groups], page=page, per_page=10, prefix="grp")
        await cb.message.edit_reply_markup(reply_markup=kb)
        await cb.answer()
        return

    if payload.startswith("grp:"):
        gid = int(payload.split(":", 1)[1])
        title = next((t for i, t in groups if i == gid), str(gid))

        sm = get_sessionmaker()
        cfg = Config.load()

        # 1) збережемо вибір користувача
        async with sm() as s:
            u = await get_or_create_user(s, cb.from_user.id)
            u.faculty_id = data["faculty_id"]
            u.course = data["course"]
            u.group_id = gid
            if not u.notify_offset_min:
                u.notify_offset_min = cfg.default_notify_offset_min
            await s.commit()

        # 2) прогрес-повідомлення
        progress_msg = await cb.message.answer("Оновлюю розклад…")

        # 3) підтягнемо розклад і синхронізуємо
        count = 0
        try:
            async with sm() as s:
                async with SourceClient(cfg) as sc:
                    html = await sc.post_filter(
                        faculty_id=data["faculty_id"],
                        course=data["course"],
                        group_id=gid
                    )
                events_dicts = list(parse_timetable(html, group_id=gid, cfg_times=cfg.lesson_times))
                new_events = [TimetableEvent(**d) for d in events_dicts]
                count = await sync_events_for_group(s, gid, new_events)
                await s.commit()

            await progress_msg.edit_text(
                f"Групу збережено: {title}\n"
                f"Завантажено занять: {count}"
            )
        except Exception:
            await progress_msg.edit_text(
                f"Групу збережено: {title}\n"
                f"Не вдалося оновити розклад зараз. Спробуйте пізніше."
            )

        # 4) Питаємо про хвилини для нагадування
        minutes_options = [1, 5, 10]  # ⬅️ ОНОВЛЕНО
        kb = simple_list_kb([(f"notify:{m}", f"{m} хв") for m in minutes_options] + [(f"notify:skip", "Залишити за замовч.")])
        await cb.message.answer("За скільки хвилин до початку пари надсилати нагадування?", reply_markup=kb)
        await state.set_state(Onb.notify)
        await cb.answer()

@router.callback_query(Onb.notify, F.data.startswith("notify:"))
async def pick_notify_offset(cb: CallbackQuery, state: FSMContext):
    cfg = Config.load()
    payload = cb.data.split(":", 1)[1]
    if payload == "skip":
        mins = cfg.default_notify_offset_min
    else:
        try:
            mins = int(payload)
        except ValueError:
            mins = cfg.default_notify_offset_min
    mins = max(1, min(mins, 120))

    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.scalar(select(User).where(User.user_id == cb.from_user.id))
        if u:
            u.notify_offset_min = mins
            await s.commit()

    await cb.message.edit_text(f"Нагадування встановлено: за {mins} хв до пари.")
    await cb.message.answer("Готово! Команди: /today, /tomorrow, /week, /next")
    await state.clear()
    await cb.answer()
