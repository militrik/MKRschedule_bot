from __future__ import annotations

import re
from typing import Iterable
from datetime import datetime, date, time

from bs4 import BeautifulSoup


# ───────────── BASIC SELECT PARSERS (студент) ─────────────

def parse_faculties(html: str) -> list[tuple[int, str]]:
    soup = BeautifulSoup(html, "lxml")
    sel = soup.select_one("#timetableform-facultyid, select[name='TimeTableForm[facultyId]']")
    out: list[tuple[int, str]] = []
    if sel:
        for opt in sel.select("option"):
            val = (opt.get("value") or "").strip()
            if val.isdigit():
                out.append((int(val), opt.text.strip()))
    return out

def parse_courses(html: str) -> list[int]:
    soup = BeautifulSoup(html, "lxml")
    sel = soup.select_one("#timetableform-course, select[name='TimeTableForm[course]']")
    out: list[int] = []
    if sel:
        for opt in sel.select("option"):
            v = (opt.get("value") or "").strip()
            if v.isdigit():
                out.append(int(v))
    return out

def parse_groups(html: str) -> list[tuple[int, str]]:
    soup = BeautifulSoup(html, "lxml")
    sel = soup.select_one("#timetableform-groupid, select[name='TimeTableForm[groupId]']")
    out: list[tuple[int, str]] = []
    if sel:
        for opt in sel.select("option"):
            v = (opt.get("value") or "").strip()
            if v.isdigit():
                out.append((int(v), opt.text.strip()))
    return out


# ───────────── BASIC SELECT PARSERS (викладач) ────────────

def parse_chairs(html: str) -> list[tuple[int, str]]:
    soup = BeautifulSoup(html, "lxml")
    sel = soup.select_one("#timetableform-chairid, select[name='TimeTableForm[chairId]']")
    out: list[tuple[int, str]] = []
    if sel:
        for opt in sel.select("option"):
            v = (opt.get("value") or "").strip()
            if v.isdigit():
                out.append((int(v), opt.text.strip()))
    return out

def parse_teachers(html: str) -> list[tuple[int, str]]:
    soup = BeautifulSoup(html, "lxml")
    sel = soup.select_one("#timetableform-teacherid, select[name='TimeTableForm[teacherId]']")
    out: list[tuple[int, str]] = []
    if sel:
        for opt in sel.select("option"):
            v = (opt.get("value") or "").strip()
            if v.isdigit():
                out.append((int(v), opt.text.strip()))
    return out


# ───────────── HELPERS (час, типи) ─────────────

_LESSON_TYPE_MAP = {
    "лк": "Лекція",
    "пз": "Практика",
    "лб": "Лабораторна",
    "кп": "КП",
    "кз": "КЗ",
    "зн": "Залік",
    "ісп": "Іспит",
    "екз": "Екзамен",
    "зал": "Залік",
}

def _parse_lesson_type(s: str | None) -> str | None:
    if not s:
        return None
    m = re.search(r"\[(.*?)\]", s)
    if not m:
        return None
    key = m.group(1).strip().lower()
    return _LESSON_TYPE_MAP.get(key, m.group(1))

def _dt_from_title(title: str) -> tuple[date, int] | None:
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})\s+(\d+)\s*пара", title, re.I)
    if not m:
        return None
    d = datetime.strptime(m.group(1), "%d.%m.%Y").date()
    num = int(m.group(2))
    return d, num

def _strip_html_lines(s: str) -> list[str]:
    from bs4 import BeautifulSoup as BS
    s = s.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    return [line.strip() for line in BS(s, "lxml").get_text("\n").splitlines() if line.strip()]

def _extract_row_times(soup: BeautifulSoup) -> dict[int, tuple[time | None, time | None]]:
    row_times: dict[int, tuple[time | None, time | None]] = {}

    def _parse_hhmm(txt: str) -> time | None:
        txt = (txt or "").strip()
        if not txt:
            return None
        try:
            hh, mm = map(int, txt.split(":", 1))
            return time(hh, mm)
        except Exception:
            return None

    for th in soup.select("table#timeTable th.headcol"):
        lesson_el = th.select_one("span.lesson")
        start_el = th.select_one("span.start")
        end_el = th.select_one("span.end")
        if not lesson_el:
            continue
        m = re.search(r"(\d+)", lesson_el.get_text(strip=True))
        if not m:
            continue
        n = int(m.group(1))
        t_start = _parse_hhmm(start_el.get_text()) if start_el else None
        t_end = _parse_hhmm(end_el.get_text()) if end_el else None
        row_times[n] = (t_start, t_end)
    return row_times


# ───────────── MAIN: STUDENT ─────────────

def parse_timetable(
    html: str,
    group_id: int,
    cfg_times: dict[int, tuple[str, str]] | None = None
) -> Iterable[dict]:
    soup = BeautifulSoup(html, "lxml")
    row_times = _extract_row_times(soup)
    popovers = soup.select('[data-toggle="popover"], [data-bs-toggle="popover"]')

    for d in popovers:
        title = d.get("data-original-title") or d.get("title") or d.get("data-title") or ""
        dt_pair = _dt_from_title(title)
        if not dt_pair:
            continue
        dt, lesson_num = dt_pair

        dc = d.get("data-content") or d.get("data-bs-content") or ""
        info_lines = _strip_html_lines(dc)
        cell_lines = _strip_html_lines(str(d))

        subject_full = None
        subject_code = None
        lesson_type = None
        auditory = None
        teacher_short = None
        teacher_full = None
        source_added = None

        if info_lines:
            subject_full_raw = info_lines[0]
            lesson_type = _parse_lesson_type(subject_full_raw)
            subject_full = re.sub(r"\s*\[.*?\]\s*$", "", subject_full_raw).strip()

            for line in info_lines[1:]:
                l = line.lower()
                if l.startswith("ауд."):
                    auditory = line.split(" ", 1)[1].strip() if " " in line else line.replace("ауд.", "").strip()
                    continue
                if l.startswith("додано"):
                    m = re.search(r"(\d{2}\.\d{2}\.\d{4})", line)
                    if m:
                        source_added = datetime.strptime(m.group(1), "%d.%m.%Y").date()
                    continue
                if re.search(r"[А-ЯІЇЄҐ][а-яіїєґ']+\s+[А-Я][а-яіїєґ']+\s+[А-Я][а-яіїєґ']+$", line):
                    teacher_full = line.strip()
                elif re.search(r"[А-ЯІЇЄҐ][а-яіїєґ']+\s+[А-Я]\.[А-Я]\.$", line):
                    teacher_short = line.strip()

        if cell_lines:
            first = re.sub(r"\[.*?\]", "", cell_lines[0]).strip()
            if 1 <= len(first) <= 15:
                subject_code = first
            if not teacher_short:
                for ln in cell_lines[1:]:
                    if re.search(r"[А-ЯІЇЄҐ][а-яіїєґ']+\s+[А-Я]\.[А-Я]\.$", ln):
                        teacher_short = ln.strip()
                        break

        t_start, t_end = row_times.get(lesson_num, (None, None))
        if (t_start is None or t_end is None) and cfg_times and (lesson_num in cfg_times):
            sh, eh = cfg_times[lesson_num]
            try:
                t_start = t_start or datetime.strptime(sh, "%H:%M").time()
                t_end = t_end or datetime.strptime(eh, "%H:%M").time()
            except Exception:
                pass

        yield {
            "group_id": group_id,
            "teacher_id": None,
            "date": dt,
            "weekday": None,
            "lesson_number": lesson_num,
            "time_start": t_start,
            "time_end": t_end,
            "subject_code": subject_code,
            "subject_full": subject_full,
            "lesson_type": lesson_type,
            "auditory": auditory,
            "teacher_short": teacher_short,
            "teacher_full": teacher_full,
            "groups_text": None,
            "source_added": source_added,
            "source_url": None,
            "source_hash": None,
            "raw_html": str(d),
        }


# ───────────── MAIN: TEACHER ─────────────

def parse_timetable_teacher(
    html: str,
    teacher_id: int,
    teacher_full_name: str | None,
    cfg_times: dict[int, tuple[str, str]] | None = None
) -> Iterable[dict]:
    soup = BeautifulSoup(html, "lxml")
    row_times = _extract_row_times(soup)
    popovers = soup.select('[data-toggle="popover"], [data-bs-toggle="popover"]')

    for d in popovers:
        title = d.get("data-original-title") or d.get("title") or d.get("data-title") or ""
        dt_pair = _dt_from_title(title)
        if not dt_pair:
            continue
        dt, lesson_num = dt_pair

        dc = d.get("data-content") or d.get("data-bs-content") or ""
        info_lines = _strip_html_lines(dc)
        cell_lines = _strip_html_lines(str(d))

        subject_full = None
        subject_code = None
        lesson_type = None
        auditory = None
        groups_text = None
        source_added = None

        if info_lines:
            subject_full_raw = info_lines[0]
            lesson_type = _parse_lesson_type(subject_full_raw)
            subject_full = re.sub(r"\s*\[.*?\]\s*$", "", subject_full_raw).strip()

            for line in info_lines[1:]:
                l = line.lower()
                if l.startswith("ауд."):
                    auditory = line.split(" ", 1)[1].strip() if " " in line else line.replace("ауд.", "").strip()
                    continue
                if l.startswith("додано"):
                    m = re.search(r"(\d{2}\.\d{2}\.\d{4})", line)
                    if m:
                        source_added = datetime.strptime(m.group(1), "%d.%m.%Y").date()
                    continue

        if cell_lines:
            first = re.sub(r"\[.*?\]", "", cell_lines[0]).strip()
            if 1 <= len(first) <= 15:
                subject_code = first

        # групи в <i>...</i>
        i_tags = BeautifulSoup(str(d), "lxml").select("i")
        if i_tags:
            groups = []
            for i in i_tags:
                t = i.get_text(" ", strip=True)
                if t:
                    groups.append(t)
            if groups:
                groups_text = ", ".join(groups)

        t_start, t_end = row_times.get(lesson_num, (None, None))
        if (t_start is None or t_end is None) and cfg_times and (lesson_num in cfg_times):
            sh, eh = cfg_times[lesson_num]
            try:
                t_start = t_start or datetime.strptime(sh, "%H:%M").time()
                t_end = t_end or datetime.strptime(eh, "%H:%M").time()
            except Exception:
                pass

        yield {
            "group_id": None,
            "teacher_id": teacher_id,
            "date": dt,
            "weekday": None,
            "lesson_number": lesson_num,
            "time_start": t_start,
            "time_end": t_end,
            "subject_code": subject_code,
            "subject_full": subject_full,
            "lesson_type": lesson_type,
            "auditory": auditory,
            "teacher_short": None,
            "teacher_full": teacher_full_name,
            "groups_text": groups_text,
            "source_added": source_added,
            "source_url": None,
            "source_hash": None,
            "raw_html": str(d),
        }
# def _normalize_ws(text: str) -> str:
#     return re.sub(r"\s+", " ", text or "").strip()
#
#
# def extract_institution_name(html: str) -> str:
#     """
#     Витягає назву закладу з головної сторінки.
#     Цілиться в <div class="header ...">КрНУ</div>, але залишаємося толерантними до зайвих пробілів.
#     Повертає чистий рядок або порожній, якщо не знайшли.
#     """
#     soup = BeautifulSoup(html, "lxml")
#     node = soup.select_one("div.header")
#     if not node:
#         # перестраховка: деякі шаблони іноді кладуть h1/h2 в топбар
#         node = soup.select_one("nav .header") or soup.select_one(".header")
#     return _normalize_ws(node.get_text()) if node else ""