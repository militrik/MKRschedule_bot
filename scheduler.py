from __future__ import annotations

import random
from datetime import datetime, timedelta, date, timezone

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from apscheduler.triggers.date import DateTrigger
from apscheduler.triggers.cron import CronTrigger
from sqlalchemy import select
from zoneinfo import ZoneInfo

from db import get_sessionmaker
from models import Group, TimetableEvent, NotificationLog, User, Teacher
from parsing.client import SourceClient
from parsing.extractors import parse_timetable, parse_timetable_teacher
from config import Config
from utils.time import now_kiev, today_kiev
from utils.formatting import EntityBuilder
from repositories import (
    distinct_group_ids_in_users,
    distinct_teacher_ids_in_users,
    upcoming_events_for_user_between,
    has_notification_around,
    zoom_for_event,
    sync_events_for_group,
    sync_events_for_teacher,
    cleanup_old_records,
)

class BotScheduler:
    def __init__(self, bot):
        self.bot = bot
        self.scheduler = AsyncIOScheduler(timezone="UTC")
        self.cfg = Config.load()
        self.group_jobs: dict[int, str] = {}
        self.teacher_jobs: dict[int, str] = {}
        self._last_scan_local = None  # Europe/Kyiv

    def start(self):
        self.scheduler.add_job(
            self._init_refresh_jobs,
            DateTrigger(run_date=datetime.now(timezone.utc) + timedelta(seconds=1)),
            id="init_refresh_jobs",
            replace_existing=True,
        )
        self.scheduler.add_job(
            self.reconcile_jobs,
            IntervalTrigger(minutes=max(1, self.cfg.refresh_reconcile_minutes)),
            id="reconcile_jobs",
            replace_existing=True,
        )
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
        self.scheduler.add_job(
            self.scan_upcoming,
            IntervalTrigger(seconds=max(1, self.cfg.scan_interval_seconds)),
            id="scan_upcoming",
            replace_existing=True,
        )
        self.scheduler.start()

    # -------------------- refresh jobs --------------------
    async def _init_refresh_jobs(self):
        sm = get_sessionmaker()
        async with sm() as s:
            group_ids = list(await distinct_group_ids_in_users(s))
            teacher_ids = list(await distinct_teacher_ids_in_users(s))
        await self._schedule_evenly(group_ids, dest="group")
        await self._schedule_evenly(teacher_ids, dest="teacher")

    async def _schedule_evenly(self, ids: list[int], dest: str):
        mapping = self.group_jobs if dest == "group" else self.teacher_jobs
        # clear previous
        for i, job_id in list(mapping.items()):
            try: self.scheduler.remove_job(job_id)
            except Exception: pass
            mapping.pop(i, None)
        if not ids:
            return
        interval_seconds = max(1, int(self.cfg.refresh_interval_hours * 3600))
        spacing = max(1, interval_seconds // len(ids))
        now = datetime.now(timezone.utc)
        for idx, _id in enumerate(sorted(ids)):
            offset = idx * spacing
            jitter = random.randint(0, max(0, self.cfg.refresh_jitter_seconds))
            next_run = now + timedelta(seconds=offset + jitter)
            job = self._schedule_job(_id, next_run, dest)
            mapping[_id] = job.id

    def _schedule_job(self, _id: int, next_run_time: datetime, dest: str):
        if dest == "group":
            job = self.scheduler.add_job(
                self.refresh_one_group,
                IntervalTrigger(
                    hours=max(1, self.cfg.refresh_interval_hours),
                    jitter=max(0, self.cfg.refresh_jitter_seconds),
                ),
                next_run_time=next_run_time,
                id=f"refresh_group_{_id}",
                replace_existing=True,
                kwargs={"group_id": _id},
            )
        else:
            job = self.scheduler.add_job(
                self.refresh_one_teacher,
                IntervalTrigger(
                    hours=max(1, self.cfg.refresh_interval_hours),
                    jitter=max(0, self.cfg.refresh_jitter_seconds),
                ),
                next_run_time=next_run_time,
                id=f"refresh_teacher_{_id}",
                replace_existing=True,
                kwargs={"teacher_id": _id},
            )
        return job

    async def reconcile_jobs(self):
        sm = get_sessionmaker()
        async with sm() as s:
            curr_gids = set(await distinct_group_ids_in_users(s))
            curr_tids = set(await distinct_teacher_ids_in_users(s))
        # groups
        sched_g = set(self.group_jobs.keys())
        for gid in sched_g - curr_gids:
            job_id = self.group_jobs.pop(gid, None)
            if job_id:
                try: self.scheduler.remove_job(job_id)
                except Exception: pass
        interval_seconds = max(1, int(self.cfg.refresh_interval_hours * 3600))
        now = datetime.now(timezone.utc)
        for gid in sorted(curr_gids - sched_g):
            offset = random.randint(0, max(0, interval_seconds - 1)) if interval_seconds > 1 else 0
            jitter = random.randint(0, max(0, self.cfg.refresh_jitter_seconds))
            next_run = now + timedelta(seconds=offset + jitter)
            job = self._schedule_job(gid, next_run, dest="group")
            self.group_jobs[gid] = job.id
        # teachers
        sched_t = set(self.teacher_jobs.keys())
        for tid in sched_t - curr_tids:
            job_id = self.teacher_jobs.pop(tid, None)
            if job_id:
                try: self.scheduler.remove_job(job_id)
                except Exception: pass
        for tid in sorted(curr_tids - sched_t):
            offset = random.randint(0, max(0, interval_seconds - 1)) if interval_seconds > 1 else 0
            jitter = random.randint(0, max(0, self.cfg.refresh_jitter_seconds))
            next_run = now + timedelta(seconds=offset + jitter)
            job = self._schedule_job(tid, next_run, dest="teacher")
            self.teacher_jobs[tid] = job.id

    # -------------------- refresh one --------------------
    async def refresh_one_group(self, group_id: int):
        sm = get_sessionmaker()
        cfg = self.cfg
        try:
            async with sm() as s, SourceClient(cfg) as sc:
                g = await s.get(Group, group_id)
                if not g:
                    return
                html = await sc.post_filter(faculty_id=g.faculty_id or 0, course=g.course or 1, group_id=g.id)
                events_dicts = list(parse_timetable(html, group_id=g.id, cfg_times=cfg.lesson_times))
                from models import TimetableEvent as E
                new_events = [E(**d) for d in events_dicts]
                await sync_events_for_group(s, g.id, new_events)  # ← диф. оновлення
                await s.commit()
        except Exception:
            pass

    async def refresh_one_teacher(self, teacher_id: int):
        sm = get_sessionmaker()
        cfg = self.cfg
        try:
            async with sm() as s, SourceClient(cfg) as sc:
                t = await s.get(Teacher, teacher_id)
                if not t:
                    return
                html = await sc.post_teacher_filter(chair_id=t.chair_id or 0, teacher_id=t.id)
                events_dicts = list(parse_timetable_teacher(html, teacher_id=t.id, teacher_full_name=t.full_name, cfg_times=cfg.lesson_times))
                from models import TimetableEvent as E
                new_events = [E(**d) for d in events_dicts]
                await sync_events_for_teacher(s, t.id, new_events)  # ← диф. оновлення
                await s.commit()
        except Exception:
            pass

    # -------------------- cleanup --------------------
    async def cleanup_old_records_job(self):
        sm = get_sessionmaker()
        cutoff_event_date = today_kiev().date() - timedelta(days=max(1, self.cfg.event_retention_days))
        cutoff_notif_dt = datetime.now(timezone.utc) - timedelta(days=max(1, self.cfg.notification_retention_days))
        async with sm() as s:
            _ = await cleanup_old_records(s, cutoff_event_date, cutoff_notif_dt)
            await s.commit()

    # -------------------- notifications --------------------
    async def scan_upcoming(self):
        sm = get_sessionmaker()
        now_local = now_kiev()

        # Кого скануємо
        async with sm() as s:
            users = list((await s.execute(select(User).where(
                (User.group_id.is_not(None)) | (User.teacher_id.is_not(None))
            ))).scalars())

        if self._last_scan_local is None:
            self._last_scan_local = now_local - timedelta(seconds=max(60, self.cfg.scan_interval_seconds))

        win_start_base = self._last_scan_local
        win_end_base = now_local

        for u in users:
            async with sm() as s:
                user_win_start = win_start_base + timedelta(minutes=u.notify_offset_min)
                user_win_end = win_end_base + timedelta(minutes=u.notify_offset_min)
                triples = await upcoming_events_for_user_between(s, u, user_win_start, user_win_end)
                for (user, e, sched_dt_local) in triples:
                    # блокування дубляжу тільки для того самого розкладу (часу)
                    if await has_notification_around(s, user.user_id, e.id, sched_dt_local, tolerance_seconds=180):
                        continue
                    try:
                        z = await zoom_for_event(s, e)
                        text, entities = self._format_notif(u, u.notify_offset_min, e, zoom_url=z)
                        await self.bot.send_message(chat_id=user.user_id, text=text, entities=entities)
                        s.add(NotificationLog(
                            user_id=user.user_id,
                            group_id=e.group_id,
                            event_id=e.id,
                            scheduled_for=sched_dt_local,
                            sent_at=datetime.now(timezone.utc),
                            status="sent",
                            error=None
                        ))
                        await s.commit()
                    except Exception as ex:
                        s.add(NotificationLog(
                            user_id=user.user_id,
                            group_id=e.group_id,
                            event_id=e.id,
                            scheduled_for=sched_dt_local,
                            sent_at=None,
                            status="failed",
                            error=str(ex)
                        ))
                        await s.commit()

        self._last_scan_local = now_local

    # --------- formatting ---------
    @staticmethod
    def _subject_display(e: TimetableEvent) -> str:
        code = (e.subject_code or "").strip()
        full = (e.subject_full or "").strip()
        return f"{code} {full}".strip() or "Заняття"

    def _format_notif(self, u: User, minutes: int, e: TimetableEvent, zoom_url: str | None = None):
        from utils.formatting import EntityBuilder
        subj = self._subject_display(e)
        lt = f" ({e.lesson_type})" if e.lesson_type else ""
        room = f", ауд. {e.auditory}" if e.auditory else ""
        extra = ""
        if u.role == "teacher":
            groups = (e.groups_text or "").strip()
            if groups:
                extra += f"\nГрупи: {groups}"
        else:
            teacher = (e.teacher_full or e.teacher_short or "").strip()
            if teacher:
                extra += f"\nВикл.: {teacher}"
        t = f"{e.time_start.strftime('%H:%M')}" if e.time_start else f"пара №{e.lesson_number}"
        zoom_line = f"\nZoom: {zoom_url}" if zoom_url else ""
        b = EntityBuilder()
        b.add(f"За {minutes} хвилин почнеться заняття з ").add_bold(subj).add(f" о {t}{room}.{extra}{zoom_line}{lt}")
        return b.build()
