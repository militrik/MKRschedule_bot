from __future__ import annotations
import re
import httpx
from datetime import date, timedelta

from config import Config
from utils.diag import write_blob


def _has_select_with_options(html: str, select_name_or_id: str) -> bool:
    # Є селект і принаймні один option з value
    id_pat = rf'id="{re.escape(select_name_or_id)}"'
    name_pat = rf'name="TimeTableForm\[{re.escape(select_name_or_id.split("-", 1)[-1])}\]"'
    return bool(
        re.search(id_pat + '|' + name_pat, html, re.I)
        and re.search(r'<option[^>]+value="[^"]+', html, re.I)
    )

def _has_courses_select(html: str) -> bool:
    return _has_select_with_options(html, "timetableform-course")

def _has_groups_select(html: str) -> bool:
    return _has_select_with_options(html, "timetableform-groupid")

def _has_teachers_select(html: str) -> bool:
    return _has_select_with_options(html, "timetableform-teacherid")


class SourceClient:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self._client: httpx.AsyncClient | None = None
        self._csrf: str | None = None
        self._last_url: str | None = None

    async def __aenter__(self):
        self._client = httpx.AsyncClient(
            timeout=30.0,
            follow_redirects=True,
            headers={
                "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                              "AppleWebKit/537.36 (KHTML, like Gecko) "
                              "Chrome/124.0 Safari/537.36",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Accept-Language": "uk-UA,uk;q=0.9,en-US;q=0.8,en;q=0.7",
            },
        )
        return self

    async def __aexit__(self, exc_type, exc, tb):
        if self._client:
            try:
                await self._client.aclose()
            finally:
                self._client = None

    # ---------------- CSRF utils ----------------
    def _extract_and_store_csrf(self, html: str) -> None:
        m = re.search(r'name="_csrf-frontend"\s+value="([^"]+)"', html)
        if not m:
            m = re.search(r'name="csrf-token"\s+content="([^"]+)"', html)
        if m:
            self._csrf = m.group(1)

    def _inject_csrf(self, data: dict) -> dict:
        if self._csrf:
            d = dict(data)
            d["_csrf-frontend"] = self._csrf
            return d
        return data

    def _csrf_headers(self, ajax: bool = False) -> dict:
        h = {}
        if self._csrf:
            h["X-CSRF-Token"] = self._csrf
        if self._last_url:
            h["Referer"] = self._last_url
        if ajax:
            h["X-Requested-With"] = "XMLHttpRequest"
        return h

    async def _ensure_group_session(self) -> str:
        """Гарантовано отримати куки+CSRF для сторінки груп перед POST."""
        url = f"{self.cfg.base_url}/time-table/group"
        r = await self._client.get(url)
        r.raise_for_status()
        self._last_url = str(r.request.url)
        self._extract_and_store_csrf(r.text)
        write_blob("student_start_auto", r.text)
        return r.text

    async def _ensure_teacher_session(self) -> str:
        """Гарантовано отримати куки+CSRF для сторінки викладачів перед POST."""
        url = f"{self.cfg.base_url}/time-table/teacher"
        r = await self._client.get(url)
        r.raise_for_status()
        self._last_url = str(r.request.url)
        self._extract_and_store_csrf(r.text)
        write_blob("teacher_start_auto", r.text)
        return r.text

    # ---------------- HOME ----------------
    async def get_home(self) -> str:
        """Головна сторінка BASE_URL — використовується для витягання назви ЗВО."""
        url = f"{self.cfg.base_url}"
        r = await self._client.get(url)
        r.raise_for_status()
        self._last_url = str(r.request.url)
        self._extract_and_store_csrf(r.text)
        write_blob("home", r.text)
        return r.text

    # ---------------- STUDENT ----------------
    async def get_start(self) -> str:
        url = f"{self.cfg.base_url}/time-table/group"
        r = await self._client.get(url)
        r.raise_for_status()
        self._last_url = str(r.request.url)
        self._extract_and_store_csrf(r.text)
        write_blob("student_start", r.text)
        return r.text

    async def post_faculty_form(
        self,
        faculty_id: int,
        dstart: date | None = None,
        dend: date | None = None,
    ) -> str:
        """
        Після вибору ФАКУЛЬТЕТУ — сервер повертає форму з курсами.
        ВАЖЛИВО: у межах ТОГО Ж клієнта робимо попередній GET, щоб були валідні куки+CSRF.
        """
        await self._ensure_group_session()

        if dstart is None:
            dstart = date.today()
        if dend is None:
            dend = dstart + timedelta(days=28)

        base_data = {
            "TimeTableForm[type]": "0",
            "TimeTableForm[facultyId]": str(faculty_id),
            "TimeTableForm[course]": "",      # reset як у фронтенді
            "TimeTableForm[groupId]": "",     # reset
            "TimeTableForm[dateStart]": dstart.strftime("%d.%m.%Y"),
            "TimeTableForm[dateEnd]": dend.strftime("%d.%m.%Y"),
        }
        data = self._inject_csrf(base_data)

        url = f"{self.cfg.base_url}/time-table/group?type=0"

        # Основний шлях — POST (ajax)
        r = await self._client.post(url, data=data, headers=self._csrf_headers(ajax=True))
        self._last_url = str(r.request.url)
        self._extract_and_store_csrf(r.text)
        write_blob("student_faculty_form_post", r.text)
        if r.status_code == 200 and _has_courses_select(r.text):
            return r.text

        # Резерв — GET з query (деякі інсталяції приймають і так)
        r = await self._client.get(url, params=base_data, headers=self._csrf_headers())
        self._last_url = str(r.request.url)
        self._extract_and_store_csrf(r.text)
        write_blob("student_faculty_form_get", r.text)
        return r.text

    async def post_group_form(
        self,
        faculty_id: int,
        course: int,
        dstart: date | None = None,
        dend: date | None = None,
    ) -> str:
        """
        Після вибору КУРСУ — сервер повертає форму з групами.
        Перед цим — обов'язковий GET для куків/CSRF.
        """
        await self._ensure_group_session()

        if dstart is None:
            dstart = date.today()
        if dend is None:
            dend = dstart + timedelta(days=28)

        base_data = {
            "TimeTableForm[type]": "0",
            "TimeTableForm[facultyId]": str(faculty_id),
            "TimeTableForm[course]": str(course),
            "TimeTableForm[groupId]": "",     # reset
            "TimeTableForm[dateStart]": dstart.strftime("%d.%m.%Y"),
            "TimeTableForm[dateEnd]": dend.strftime("%d.%m.%Y"),
        }
        data = self._inject_csrf(base_data)

        url = f"{self.cfg.base_url}/time-table/group?type=0"

        r = await self._client.post(url, data=data, headers=self._csrf_headers(ajax=True))
        self._last_url = str(r.request.url)
        self._extract_and_store_csrf(r.text)
        write_blob("student_group_form_post", r.text)
        if r.status_code == 200 and _has_groups_select(r.text):
            return r.text

        r = await self._client.get(url, params=base_data, headers=self._csrf_headers())
        self._last_url = str(r.request.url)
        self._extract_and_store_csrf(r.text)
        write_blob("student_group_form_get", r.text)
        return r.text

    async def post_filter(
        self,
        faculty_id: int,
        course: int,
        group_id: int,
        dstart: date | None = None,
        dend: date | None = None,
    ) -> str:
        """Фінальний запит — розклад групи. Також гарантуємо сесію GET'ом."""
        await self._ensure_group_session()

        url = f"{self.cfg.base_url}/time-table/group?type=0"
        if dstart is None:
            dstart = date.today()
        if dend is None:
            dend = dstart + timedelta(days=28)

        base_data = {
            "TimeTableForm[type]": "0",
            "TimeTableForm[facultyId]": str(faculty_id),
            "TimeTableForm[course]": str(course),
            "TimeTableForm[groupId]": str(group_id),
            "TimeTableForm[dateStart]": dstart.strftime("%d.%m.%Y"),
            "TimeTableForm[dateEnd]": dend.strftime("%d.%m.%Y"),
        }
        data = self._inject_csrf(base_data)

        r = await self._client.post(url, data=data, headers=self._csrf_headers(ajax=True))
        r.raise_for_status()
        self._last_url = str(r.request.url)
        self._extract_and_store_csrf(r.text)
        write_blob("student_filter_post", r.text)
        return r.text

    # ---------------- TEACHER ----------------
    async def get_teacher_start(self) -> str:
        url = f"{self.cfg.base_url}/time-table/teacher"
        r = await self._client.get(url)
        r.raise_for_status()
        self._last_url = str(r.request.url)
        self._extract_and_store_csrf(r.text)
        write_blob("teacher_start", r.text)
        return r.text

    async def post_teacher_form(
        self,
        chair_id: int,
        dstart: date | None = None,
        dend: date | None = None,
    ) -> str:
        """Після вибору КАФЕДРИ — список викладачів. Спочатку GET для сесії."""
        await self._ensure_teacher_session()

        if dstart is None:
            dstart = date.today()
        if dend is None:
            dend = dstart + timedelta(days=28)

        base_data = {
            "TimeTableForm[type]": "0",
            "TimeTableForm[chairId]": str(chair_id),
            "TimeTableForm[teacherId]": "",   # reset
            "TimeTableForm[dateStart]": dstart.strftime("%d.%m.%Y"),
            "TimeTableForm[dateEnd]": dend.strftime("%d.%m.%Y"),
        }
        data = self._inject_csrf(base_data)
        url = f"{self.cfg.base_url}/time-table/teacher?type=0"

        r = await self._client.post(url, data=data, headers=self._csrf_headers(ajax=True))
        self._last_url = str(r.request.url)
        self._extract_and_store_csrf(r.text)
        write_blob("teacher_form_post", r.text)
        if r.status_code == 200 and _has_teachers_select(r.text):
            return r.text

        r = await self._client.get(url, params=base_data, headers=self._csrf_headers())
        self._last_url = str(r.request.url)
        self._extract_and_store_csrf(r.text)
        write_blob("teacher_form_get", r.text)
        return r.text

    async def post_teacher_filter(
        self,
        chair_id: int,
        teacher_id: int,
        dstart: date | None = None,
        dend: date | None = None,
    ) -> str:
        """Фінальний запит — розклад викладача. Також гарантуємо сесію GET'ом."""
        await self._ensure_teacher_session()

        url = f"{self.cfg.base_url}/time-table/teacher?type=0"
        if dstart is None:
            dstart = date.today()
        if dend is None:
            dend = dstart + timedelta(days=28)

        base_data = {
            "TimeTableForm[type]": "0",
            "TimeTableForm[chairId]": str(chair_id),
            "TimeTableForm[teacherId]": str(teacher_id),
            "TimeTableForm[dateStart]": dstart.strftime("%d.%m.%Y"),
            "TimeTableForm[dateEnd]": dend.strftime("%d.%m.%Y"),
        }
        data = self._inject_csrf(base_data)

        r = await self._client.post(url, data=data, headers=self._csrf_headers(ajax=True))
        r.raise_for_status()
        self._last_url = str(r.request.url)
        self._extract_and_store_csrf(r.text)
        write_blob("teacher_filter_post", r.text)
        return r.text
