from __future__ import annotations

import re
from typing import Iterable
from datetime import datetime, date, time

from bs4 import BeautifulSoup


# ────────────────────────────── BASIC SELECT PARSERS ──────────────────────────

def parse_faculties(html: str) -> list[tuple[int, str]]:
    """Повертає список (id, назва) факультетів із селекта #timetableform-facultyid."""
    soup = BeautifulSoup(html, "lxml")
    sel = soup.select_one("#timetableform-facultyid")
    out: list[tuple[int, str]] = []
    if sel:
        for opt in sel.select("option"):
            val = (opt.get("value") or "").strip()
            if val.isdigit():
                out.append((int(val), opt.text.strip()))
    return out

def parse_courses(html: str) -> list[int]:
    """Повертає список можливих курсів із селекта #timetableform-course."""
    soup = BeautifulSoup(html, "lxml")
    sel = soup.select_one("#timetableform-course")
    out: list[int] = []
    if sel:
        for opt in sel.select("option"):
            v = (opt.get("value") or "").strip()
            if v.isdigit():
                out.append(int(v))
    return out

def parse_groups(html: str) -> list[tuple[int, str]]:
    """Повертає список (id, назва) груп із селекта #timetableform-groupid."""
    soup = BeautifulSoup(html, "lxml")
    sel = soup.select_one("#timetableform-groupid")
    out: list[tuple[int, str]] = []
    if sel:
        for opt in sel.select("option"):
            v = (opt.get("value") or "").strip()
            if v.isdigit():
                out.append((int(v), opt.text.strip()))
    return out


# ────────────────────────────── HELPERS ───────────────────────────────────────

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
    """
    Приклад title: "01.09.2025 5 пара"
    """
    m = re.search(r"(\d{2}\.\d{2}\.\d{4})\s+(\d+)\s*пара", title, re.I)
    if not m:
        return None
    d = datetime.strptime(m.group(1), "%d.%m.%Y").date()
    num = int(m.group(2))
    return d, num

def _strip_html_lines(s: str) -> list[str]:
    """Розбити HTML на рядки: <br> -> \n, прибрати теги, повернути не-порожні рядки."""
    s = s.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    return [line.strip() for line in BeautifulSoup(s, "lxml").get_text("\n").splitlines() if line.strip()]

def _extract_row_times(soup: BeautifulSoup) -> dict[int, tuple[time | None, time | None]]:
    """
    Зчитати час для кожного номера пари з лівої колонки таблиці:
      <th class="headcol">
        <span class="lesson">5 пара</span>
        <span class="start">13:35</span>
        <span class="end">14:55</span>
      </th>
    Повертає {lesson_number: (start_time, end_time)}. Якщо чогось немає — None.
    """
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


# ────────────────────────────── MAIN: TIMETABLE ───────────────────────────────

def parse_timetable(
    html: str,
    group_id: int,
    cfg_times: dict[int, tuple[str, str]] | None = None
) -> Iterable[dict]:
    """
    Парсер табличного розкладу:
      • дату й номер пари беремо з title/data-title/data-original-title;
      • ПОВНУ назву — з першого рядка data-content (без суфікса [Лк]/[Пз]...);
      • АБРЕВІАТУРУ (subject_code) — з першого видимого рядка всередині клітинки;
      • ЧАС пари — пріоритетно з лівої колонки (th.headcol), fallback → cfg_times.
    Повертає словники під створення TimetableEvent.
    """
    soup = BeautifulSoup(html, "lxml")

    # 1) Фактичні часи пар зі сторінки
    row_times = _extract_row_times(soup)

    # 2) Клітинки з поповерами (Bootstrap 3/4/5)
    popovers = soup.select('[data-toggle="popover"], [data-bs-toggle="popover"]')

    for d in popovers:
        # Дата + номер пари
        title = d.get("data-original-title") or d.get("title") or d.get("data-title") or ""
        dt_pair = _dt_from_title(title)
        if not dt_pair:
            continue
        dt, lesson_num = dt_pair

        # Вміст поповера
        dc = d.get("data-content") or d.get("data-bs-content") or ""
        info_lines = _strip_html_lines(dc)

        # Видимий короткий текст у клітинці (перший рядок — абревіатура)
        cell_lines = _strip_html_lines(str(d))

        subject_full = None
        subject_code = None
        lesson_type = None
        auditory = None
        teacher_short = None
        teacher_full = None
        source_added = None

        # --- повна назва + тип заняття з data-content
        if info_lines:
            # 1-й рядок: повна назва; приберемо суфікс типу в дужках квадратних
            subject_full_raw = info_lines[0]
            lesson_type = _parse_lesson_type(subject_full_raw)
            subject_full = re.sub(r"\s*\[.*?\]\s*$", "", subject_full_raw).strip()

            # інші рядки — шукаємо аудиторію, викладача, "Додано"
            for line in info_lines[1:]:
                l = line.lower()

                # Аудиторія: "ауд. 2411" або "Аудиторія: ..."
                if l.startswith("ауд."):
                    auditory = line.split(" ", 1)[1].strip() if " " in line else line.replace("ауд.", "").strip()
                    continue
                if l.startswith("ауд"):
                    parts = line.split(":", 1)
                    if len(parts) == 2 and parts[0].strip().lower().startswith("ауд"):
                        auditory = parts[1].strip()
                        continue

                # "Додано: 27.08.2025"
                if l.startswith("додано"):
                    m = re.search(r"(\d{2}\.\d{2}\.\d{4})", line)
                    if m:
                        source_added = datetime.strptime(m.group(1), "%d.%m.%Y").date()
                    continue

                # Викладач (короткий/повний формат)
                if re.search(r"[А-ЯІЇЄҐ][а-яіїєґ']+\s+[А-Я]\.[А-Я]\.$", line):
                    teacher_short = line.strip()
                elif re.search(r"[А-ЯІЇЄҐ][а-яіїєґ']+\s+[А-Я][а-яіїєґ']+\s+[А-Я][а-яіїєґ']+$", line):
                    teacher_full = line.strip()

        # --- абревіатура з першого видимого рядка клітинки (до [..])
        if cell_lines:
            first = re.sub(r"\[.*?\]", "", cell_lines[0]).strip()
            if 1 <= len(first) <= 15:
                subject_code = first
            # інколи короткі ініціали викладача є тут
            if not teacher_short:
                for ln in cell_lines[1:]:
                    if re.search(r"[А-ЯІЇЄҐ][а-яіїєґ']+\s+[А-Я]\.[А-Я]\.$", ln):
                        teacher_short = ln.strip()
                        break

        # --- час: зі сторінки, інакше — з cfg_times
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
            "source_added": source_added,
            "source_url": None,
            "source_hash": None,
            "raw_html": str(d),
        }
