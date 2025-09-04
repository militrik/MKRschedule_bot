from __future__ import annotations
from aiogram import Router, F
from aiogram.filters import Command
from aiogram.types import Message, CallbackQuery, InlineKeyboardMarkup, InlineKeyboardButton
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.context import FSMContext

from db import get_sessionmaker
from models import User, Teacher, TimetableEvent
from parsing.client import SourceClient
from parsing.extractors import (
    parse_faculties, parse_courses, parse_groups,
    parse_chairs, parse_teachers,
    parse_timetable, parse_timetable_teacher
)
from config import Config
from repositories import (
    upsert_faculties, upsert_groups, upsert_chairs, upsert_teachers,
    sync_events_for_group, sync_events_for_teacher
)
from keyboards import paginated_kb, main_menu_kb
from utils.diag import log

router = Router(name="onboarding")

MINUTES_OPTIONS = [1, 5, 10]
LIST_PER_PAGE = 10  # скільки елементів на сторінку в інлайн-клавіатурі


class StartFSM(StatesGroup):
    role = State()
    # student
    faculty = State()
    course = State()
    group = State()
    # teacher
    chair = State()
    teacher = State()
    # common
    notify = State()


def role_keyboard():
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🎓 Я студент", callback_data="role:student")],
        [InlineKeyboardButton(text="🧑‍🏫 Я викладач", callback_data="role:teacher")],
    ])
    return kb


# --------- helpers: pagination ----------
def _page_clamp(total: int, page: int, per_page: int) -> int:
    if total <= 0:
        return 0
    pages = (total + per_page - 1) // per_page
    if page < 0:
        page = 0
    if page >= pages:
        page = pages - 1
    return page


# =======================================
#                 /start
# =======================================
@router.message(Command("start"))
async def start_cmd(message: Message, state: FSMContext):
    await state.clear()
    # Було 2 однакових повідомлення — залишаємо ОДНЕ з inline-вибором ролі
    await message.answer("Будь ласка, оберіть роль:", reply_markup=role_keyboard())
    await state.set_state(StartFSM.role)


@router.callback_query(StartFSM.role, F.data.startswith("role:"))
async def pick_role(cb: CallbackQuery, state: FSMContext):
    role = cb.data.split(":", 1)[1]
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.get(User, cb.from_user.id)
        if not u:
            u = User(user_id=cb.from_user.id, role=role)
            s.add(u)
        else:
            u.role = role
            if role == "student":
                u.teacher_id = None; u.chair_id = None
            else:
                u.group_id = None; u.faculty_id = None; u.course = None
        await s.commit()

    await cb.message.edit_text("Роль збережено.")
    if role == "student":
        await _student_flow_start(cb, state)
    else:
        await _teacher_flow_start(cb, state)
    await cb.answer()


# =======================================
#              STUDENT FLOW
# =======================================
async def _student_flow_start(cb: CallbackQuery, state: FSMContext):
    cfg = Config.load()
    sm = get_sessionmaker()
    async with sm() as s, SourceClient(cfg) as sc:
        html = await sc.get_start()
        faculties = parse_faculties(html)
        await upsert_faculties(s, faculties)
        await s.commit()

    log(f"faculties: {len(faculties)}")
    await state.update_data(fac_list=faculties, fac_page=0)

    kb = paginated_kb([(str(fid), title) for fid, title in faculties],
                      prefix="fac", per_page=LIST_PER_PAGE, page=0)
    await cb.message.answer("Оберіть факультет:", reply_markup=kb)
    await state.set_state(StartFSM.faculty)


@router.callback_query(StartFSM.faculty)
async def pick_faculty(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    fac_list: list[tuple[int, str]] = data.get("fac_list", [])
    page = int(data.get("fac_page", 0))

    payload = cb.data

    # пагінація
    if payload == "fac:__prev__" or payload == "fac:__next__":
        delta = -1 if payload.endswith("__prev__") else +1
        page = _page_clamp(len(fac_list), page + delta, LIST_PER_PAGE)
        await state.update_data(fac_page=page)
        kb = paginated_kb([(str(fid), title) for fid, title in fac_list],
                          prefix="fac", per_page=LIST_PER_PAGE, page=page)
        await cb.message.edit_reply_markup(reply_markup=kb)
        await cb.answer()
        return

    if not payload.startswith("fac:"):
        await cb.answer()
        return

    faculty_id = int(payload.split(":", 1)[1])
    await state.update_data(faculty_id=faculty_id)

    # тягнемо курси для обраного факультету
    cfg = Config.load()
    async with SourceClient(cfg) as sc:
        html = await sc.post_faculty_form(faculty_id=faculty_id)
        courses = parse_courses(html)
    if not courses:
        courses = [1, 2, 3, 4]

    log(f"courses for fac {faculty_id}: {len(courses)}")
    await state.update_data(courses=courses)

    kb = paginated_kb([(str(c), f"{c} курс") for c in courses],
                      prefix="crs", per_page=LIST_PER_PAGE, page=0)
    await cb.message.edit_text("Оберіть курс:", reply_markup=kb)
    await state.set_state(StartFSM.course)
    await cb.answer()


@router.callback_query(StartFSM.course)
async def pick_course(cb: CallbackQuery, state: FSMContext):
    payload = cb.data
    if payload in ("crs:__prev__", "crs:__next__"):
        await cb.answer()
        return
    if not payload.startswith("crs:"):
        await cb.answer()
        return

    course = int(payload.split(":", 1)[1])
    st = await state.get_data()
    faculty_id = st["faculty_id"]

    cfg = Config.load()
    async with SourceClient(cfg) as sc:
        html = await sc.post_group_form(faculty_id=faculty_id, course=course)
        groups = parse_groups(html)

    await state.update_data(course=course, group_list=groups, group_page=0)

    log(f"groups for fac {faculty_id} course {course}: {len(groups)}")
    kb = paginated_kb([(str(gid), title) for gid, title in groups],
                      prefix="grp", per_page=LIST_PER_PAGE, page=0)
    await cb.message.edit_text("Оберіть групу:", reply_markup=kb)
    await state.set_state(StartFSM.group)
    await cb.answer()


@router.callback_query(StartFSM.group)
async def pick_group(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    groups: list[tuple[int, str]] = data.get("group_list", [])
    page = int(data.get("group_page", 0))
    payload = cb.data

    # пагінація груп
    if payload in ("grp:__prev__", "grp:__next__"):
        delta = -1 if payload.endswith("__prev__") else +1
        page = _page_clamp(len(groups), page + delta, LIST_PER_PAGE)
        await state.update_data(group_page=page)
        kb = paginated_kb([(str(gid), title) for gid, title in groups],
                          prefix="grp", per_page=LIST_PER_PAGE, page=page)
        await cb.message.edit_reply_markup(reply_markup=kb)
        await cb.answer()
        return

    if not payload.startswith("grp:"):
        await cb.answer()
        return

    group_id = int(payload.split(":", 1)[1])
    faculty_id = data["faculty_id"]
    course = data["course"]

    # зберігаємо вибір користувача
    sm = get_sessionmaker()
    cfg = Config.load()
    async with sm() as s:
        u = await s.get(User, cb.from_user.id)
        u.role = "student"
        u.faculty_id = faculty_id
        u.course = course
        u.group_id = group_id
        await s.commit()

    # завантаження розкладу
    async with sm() as s, SourceClient(cfg) as sc:
        html = await sc.post_filter(faculty_id=faculty_id, course=course, group_id=group_id)
        events_dicts = list(parse_timetable(html, group_id=group_id, cfg_times=cfg.lesson_times))
        new_events = [TimetableEvent(**d) for d in events_dicts]
        await sync_events_for_group(s, group_id, new_events)
        await s.commit()

    # вибір хвилин нагадувань
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{m} хв", callback_data=f"nm:{m}")] for m in MINUTES_OPTIONS
    ])
    await cb.message.edit_text("За скільки хвилин нагадувати перед парою?", reply_markup=kb)
    await state.set_state(StartFSM.notify)
    await cb.answer()


# =======================================
#              TEACHER FLOW
# =======================================
async def _teacher_flow_start(cb: CallbackQuery, state: FSMContext):
    cfg = Config.load()
    sm = get_sessionmaker()
    async with sm() as s, SourceClient(cfg) as sc:
        html = await sc.get_teacher_start()
        chairs = parse_chairs(html)
        await upsert_chairs(s, chairs)
        await s.commit()

    log(f"chairs: {len(chairs)}")
    await state.update_data(chair_list=chairs, chr_page=0)

    kb = paginated_kb([(str(cid), title) for cid, title in chairs],
                      prefix="chr", per_page=LIST_PER_PAGE, page=0)
    await cb.message.answer("Оберіть кафедру:", reply_markup=kb)
    await state.set_state(StartFSM.chair)


@router.callback_query(StartFSM.chair)
async def pick_chair(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    chairs: list[tuple[int, str]] = data.get("chair_list", [])
    page = int(data.get("chr_page", 0))
    payload = cb.data

    if payload in ("chr:__prev__", "chr:__next__"):
        delta = -1 if payload.endswith("__prev__") else +1
        page = _page_clamp(len(chairs), page + delta, LIST_PER_PAGE)
        await state.update_data(chr_page=page)
        kb = paginated_kb([(str(cid), title) for cid, title in chairs],
                          prefix="chr", per_page=LIST_PER_PAGE, page=page)
        await cb.message.edit_reply_markup(reply_markup=kb)
        await cb.answer()
        return

    if not payload.startswith("chr:"):
        await cb.answer()
        return

    chair_id = int(payload.split(":", 1)[1])
    await state.update_data(chair_id=chair_id)

    cfg = Config.load()
    sm = get_sessionmaker()
    async with sm() as s, SourceClient(cfg) as sc:
        html = await sc.post_teacher_form(chair_id=chair_id)
        teachers = parse_teachers(html)
        await upsert_teachers(s, chair_id, teachers)
        await s.commit()

    log(f"teachers for chair {chair_id}: {len(teachers)}")
    await state.update_data(teacher_list=teachers, tch_page=0)

    kb = paginated_kb([(str(tid), fio) for tid, fio in teachers],
                      prefix="tch", per_page=LIST_PER_PAGE, page=0)
    await cb.message.edit_text("Оберіть викладача:", reply_markup=kb)
    await state.set_state(StartFSM.teacher)
    await cb.answer()


@router.callback_query(StartFSM.teacher)
async def pick_teacher(cb: CallbackQuery, state: FSMContext):
    data = await state.get_data()
    tlist: list[tuple[int, str]] = data.get("teacher_list", [])
    page = int(data.get("tch_page", 0))
    payload = cb.data

    if payload in ("tch:__prev__", "tch:__next__"):
        delta = -1 if payload.endswith("__prev__") else +1
        page = _page_clamp(len(tlist), page + delta, LIST_PER_PAGE)
        await state.update_data(tch_page=page)
        kb = paginated_kb([(str(tid), fio) for tid, fio in tlist],
                          prefix="tch", per_page=LIST_PER_PAGE, page=page)
        await cb.message.edit_reply_markup(reply_markup=kb)
        await cb.answer()
        return

    if not payload.startswith("tch:"):
        await cb.answer()
        return

    teacher_id = int(payload.split(":", 1)[1])
    chair_id = data["chair_id"]

    sm = get_sessionmaker()
    cfg = Config.load()

    async with sm() as s:
        u = await s.get(User, cb.from_user.id)
        u.role = "teacher"
        u.chair_id = chair_id
        u.teacher_id = teacher_id
        await s.commit()

        t = await s.get(Teacher, teacher_id)
        teacher_full = t.full_name if t else None

    async with sm() as s, SourceClient(cfg) as sc:
        html = await sc.post_teacher_filter(chair_id=chair_id, teacher_id=teacher_id)
        events_dicts = list(parse_timetable_teacher(html, teacher_id=teacher_id, teacher_full_name=teacher_full, cfg_times=cfg.lesson_times))
        new_events = [TimetableEvent(**d) for d in events_dicts]
        await sync_events_for_teacher(s, teacher_id, new_events)
        await s.commit()

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text=f"{m} хв", callback_data=f"nm:{m}")] for m in MINUTES_OPTIONS
    ])
    await cb.message.edit_text("За скільки хвилин нагадувати перед парою?", reply_markup=kb)
    await state.set_state(StartFSM.notify)
    await cb.answer()


# =======================================
#               NOTIFY
# =======================================
@router.callback_query(StartFSM.notify, F.data.startswith("nm:"))
async def pick_notify(cb: CallbackQuery, state: FSMContext):
    minutes = int(cb.data.split(":", 1)[1])
    sm = get_sessionmaker()
    async with sm() as s:
        u = await s.get(User, cb.from_user.id)
        u.notify_offset_min = minutes
        await s.commit()
    await state.clear()
    # Після завершення налаштувань показуємо постійну клавіатуру-меню
    await cb.message.answer(
        f"Готово!\nВаш час нагадувань: {minutes} хв.\n"
        f"Доступні команди: /today, /tomorrow, /week, /next, /help",
        reply_markup=main_menu_kb()
    )
