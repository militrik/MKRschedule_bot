from __future__ import annotations
import os
import httpx
from bs4 import BeautifulSoup, UnicodeDammit
from typing import Optional
from urllib.parse import urljoin
from config import Config

class SourceClient:
    """
    2 режими:
    - офлайн: читає локальні файли з OFFLINE_FIXTURES_DIR (автовизначення кодування)
    - онлайн : ходить на BASE_URL; сумісний і зі старим httpx (без proxies/http2), і з новим
    """
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.client: httpx.AsyncClient | None = None

    async def __aenter__(self):
        # Сумісність зі старими httpx (без proxies/http2)
        try:
            self.client = httpx.AsyncClient(
                timeout=30,
                http2=True,
                proxies=(self.cfg.http_proxy or None),
            )
        except TypeError:
            self.client = httpx.AsyncClient(timeout=30)
        return self

    async def __aexit__(self, *exc):
        if self.client:
            await self.client.aclose()

    # ---------- URL helper ----------
    def _entry_url(self) -> str:
        base = self.cfg.base_url.rstrip('/') + '/'
        if "time-table/group" in base:
            return base if "?" in base else base + "?type=0"
        return urljoin(base, "time-table/group?type=0")

    # ---------- OFFLINE ----------
    def _offline_read(self, name: str) -> Optional[str]:
        d = self.cfg.offline_fixtures_dir
        if not d:
            return None
        path = os.path.join(d, name)
        if os.path.isfile(path):
            with open(path, "rb") as f:
                raw = f.read()
            return UnicodeDammit(raw).unicode_markup
        return None

    async def get_start_page(self) -> str:
        offline = self._offline_read("start.html")
        if offline is not None:
            return offline
        url = self._entry_url()
        r = await self.client.get(url)
        r.raise_for_status()
        return r.text

    async def post_filter(self, faculty_id: int | None, course: int | None, group_id: int | None) -> str:
        if self.cfg.offline_fixtures_dir:
            if group_id:
                html = self._offline_read("selected_group.html")
                if html: return html
            if course:
                html = self._offline_read("selected_course.html")
                if html: return html
            if faculty_id:
                html = self._offline_read("selected_faculty.html")
                if html: return html
            start = self._offline_read("start.html")
            if start: return start

        start = await self.get_start_page()
        soup = BeautifulSoup(start, "lxml")
        meta_token = soup.find("meta", {"name": "csrf-token"})
        token = meta_token["content"] if meta_token else None

        url = self._entry_url()
        data = {}
        if faculty_id is not None:
            data["TimeTableForm[facultyId]"] = str(faculty_id)
        if course is not None:
            data["TimeTableForm[course]"] = str(course)
        if group_id is not None:
            data["TimeTableForm[groupId]"] = str(group_id)
        if token:
            data["_csrf-frontend"] = token

        headers = {"X-CSRF-Token": token} if token else {}

        r = await self.client.post(url, data=data, headers=headers)
        r.raise_for_status()
        return r.text
