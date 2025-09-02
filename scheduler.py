from __future__ import annotations

import random
from datetime import datetime, timedelta
from typing import Dict

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from zoneinfo import ZoneInfo

from db import get_sessionmaker
from models import Group, TimetableEvent, NotificationLog, User
from parsing.client import SourceClient
from parsing.extractors import parse_timetable
from config import Config
from utils.time import now_kiev, today_kiev
from utils.formatting import EntityBuilder
from repositories import (
    distinct_group_ids_in_users,
    upcoming_events_window,
    has_notification,
    zoom_for_event,
    sync_events_for_group,
    cleanup_old_records,
)

class BotScheduler:
    """
    Задачі:
      • Фазовані оновлення розкладу для кожної групи (щоб не бити джерело одночасно).
      • Реконсиліація списку груп.
      • Щоденний клінап історії за retention.
      • Сканер нагадувань.
    """

    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self.cfg = Config.load()
        self.group_jobs: Dict[int, str] = {}  # group_id -> job.id

    def start(self):
        # 1) Перший розклад групових апдейтів (стагеринг)
        self.scheduler.add_job(
            self._init_group_jobs,
            DateTrigger(run_date=datetime.utcnow() + timedelta(seconds=1)),
            id="init_group_jobs",
            replace_existing=True,
        )

        # 2) Періодична реконсиліація
        self.scheduler.add_job(
            self.reconcile_group_jobs,
            IntervalTrigger(minutes=max(1, self.cfg.refresh_reconcile_minutes)),
            id="reconcile_group_jobs",
            replace_existing=True,
        )

        # 3) Щоденне очищення БД (за замовчанням 03:30 за Києвом)
        self.scheduler.add_job(
            self.cleanup_old_records_job,
            CronTrigger(
                hour=self.cfg.cleanup_at_hh,
                minute=self.cfg.cleanup_at_mm,
                timezone=ZoneInfo(self.cfg.tz),
            ),
            id="cleanup_old_records",
            replace_existing=True,
        )

        # 4) Сканер нагадувань
        self.scheduler.add_job(
            self.scan_upcoming,
            IntervalTrigger(seconds=max(1, self.cfg.scan_interval_seconds)),
            id="scan_upcoming",
            replace_existing=True,
        )

        self.scheduler.start()

    # -------------------- ІНІЦІАЛЬНИЙ РОЗКЛАД --------------------
    async def _init_group_jobs(self):
        sm = get_sessionmaker()
        async with sm() as s:
            group_ids = list(await distinct_group_ids_in_users(s))
        await self._schedule_group_refreshes_even(group_ids)

    async def _schedule_group_refreshes_even(self, group_ids: list[int]):
        # Приберемо попередні
        for gid, job_id in list(self.group_jobs.items()):
            try:
                self.scheduler.remove_job(job_id)
            except Exception:
                pass
            self.group_jobs.pop(gid, None)

        if not group_ids:
            return

        interval_seconds = max(1, int(self.cfg.refresh_interval_hours * 3600))
        spacing = max(1, interval_seconds // len(group_ids))

        now = datetime.utcnow()
        for idx, gid in enumerate(sorted(group_ids)):
            offset = idx * spacing
            jitter = random.randint(0, max(0, self.cfg.refresh_jitter_seconds))
            next_run = now + timedelta(seconds=offset + jitter)
            job = self._schedule_group_job(gid, next_run)
            self.group_jobs[gid] = job.id

    def _schedule_group_job(self, group_id: int, next_run_time: datetime):
        job = self.scheduler.add_job(
            self.refresh_one_group,
            IntervalTrigger(
                hours=max(1, self.cfg.refresh_interval_hours),
                jitter=max(0, self.cfg.refresh_jitter_seconds),
            ),
            next_run_time=next_run_time,
            id=f"refresh_group_{group_id}",
            replace_existing=True,
            kwargs={"group_id": group_id},
        )
        return job

    # -------------------- РЕКОНСИЛІАЦІЯ --------------------
    async def reconcile_group_jobs(self):
        sm = get_sessionmaker()
        async with sm() as s:
            current_group_ids = set(await distinct_group_ids_in_users(s))

        scheduled_ids = set(self.group_jobs.keys())
        to_add = current_group_ids - scheduled_ids
        to_remove = scheduled_ids - current_group_ids

        for gid in to_remove:
            job_id = self.group_jobs.pop(gid, None)
            if job_id:
                try:
                    self.scheduler.remove_job(job_id)
                except Exception:
                    pass

        if to_add:
            interval_seconds = max(1, int(self.cfg.refresh_interval_hours * 3600))
            now = datetime.utcnow()
            for gid in sorted(to_add):
                offset = random.randint(0, max(0, interval_seconds - 1)) if interval_seconds > 1 else 0
                jitter = random.randint(0, max(0, self.cfg.refresh_jitter_seconds))
                next_run = now + timedelta(seconds=offset + jitter)
                job = self._schedule_group_job(gid, next_run)
                self.group_jobs[gid] = job.id

    # -------------------- ОНОВЛЕННЯ ОДНІЄЇ ГРУПИ --------------------
    async def refresh_one_group(self, group_id: int):
        sm = get_sessionmaker()
        cfg = self.cfg
        try:
            async with sm() as s:
                g = await s.get(Group, group_id)
                if not g:
                    return
                async with SourceClient(cfg) as sc:
                    html = await sc.post_filter(faculty_id=g.faculty_id, course=g.course, group_id=g.id)
                events_dicts = list(parse_timetable(html, group_id=g.id, cfg_times=cfg.lesson_times))
                from models import TimetableEvent as E
                new_events = [E(**d) for d in events_dicts]
                await sync_events_for_group(s, g.id, new_events)
                g.last_checked_at = datetime.utcnow()
                await s.commit()
        except Exception:
            # тихо ігноруємо; наступна спроба відбудеться за розкладом
            pass

    # -------------------- КЛІНАП --------------------
    async def cleanup_old_records_job(self):
        """Щоденне видалення старих логів і подій за політикою retention."""
        sm = get_sessionmaker()
        cutoff_event_date = today_kiev().date() - timedelta(days=max(1, self.cfg.event_retention_days))
        cutoff_notif_dt = datetime.utcnow() - timedelta(days=max(1, self.cfg.notification_retention_days))
        async with sm() as s:
            _res = await cleanup_old_records(s, cutoff_event_date, cutoff_notif_dt)
            await s.commit()
        # за бажанням можна залогувати _res

    # -------------------- Нагадування --------------------
    async def scan_upcoming(self):
        sm = get_sessionmaker()
        kiev_now = now_kiev()
        async with sm() as s:
            users = list((await s.execute(select(User).where(User.group_id.is_not(None)))).scalars())
        for u in users:
            async with sm() as s:
                triples = await upcoming_events_window(s, kiev_now, u.notify_offset_min)
                for (user, e, sched) in triples:
                    if await has_notification(s, user.user_id, e.id):
                        continue
                    try:
                        z = await zoom_for_event(s, e)
                        text, entities = self._format_notif(u.notify_offset_min, e, zoom_url=z)
                        await self.bot.send_message(chat_id=user.user_id, text=text, entities=entities)
                        s.add(NotificationLog(
                            user_id=user.user_id,
                            group_id=e.group_id,
                            event_id=e.id,
                            scheduled_for=sched,
                            sent_at=datetime.utcnow(),
                            status="sent",
                            error=None
                        ))
                        await s.commit()
                    except Exception as ex:
                        s.add(NotificationLog(
                            user_id=user.user_id,
                            group_id=e.group_id,
                            event_id=e.id,
                            scheduled_for=sched,
                            sent_at=None,
                            status="failed",
                            error=str(ex)
                        ))
                        await s.commit()

    # ---------- утиліти форматування ----------
    @staticmethod
    def _subject_display(e: TimetableEvent) -> str:
        code = (e.subject_code or "").strip()
        full = (e.subject_full or "").strip()
        if code and full:
            return f"{code} {full}"
        return full or code or "Заняття"

    @staticmethod
    def _teacher_display(e: TimetableEvent) -> str | None:
        """Перевага повному ПІБ, інакше скорочення."""
        return (e.teacher_full or e.teacher_short or "").strip() or None

    def _format_notif(self, minutes: int, e: TimetableEvent, zoom_url: str | None = None):
        subj = self._subject_display(e)
        lt = f" ({e.lesson_type})" if e.lesson_type else ""
        room = f", ауд. {e.auditory}" if e.auditory else ""
        teacher = self._teacher_display(e)
        teach = f"\nВикл.: {teacher}" if teacher else ""
        t = f"{e.time_start.strftime('%H:%M')}" if e.time_start else f"пара №{e.lesson_number}"
        zoom_line = f"\nZoom: {zoom_url}" if zoom_url else ""
        b = EntityBuilder()
        b.add(f"За {minutes} хвилин почнеться заняття з ").add_bold(subj).add(f" о {t}{room}.{teach}{zoom_line}{lt}")
        return b.build()
