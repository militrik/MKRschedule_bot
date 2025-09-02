from dataclasses import dataclass
import os

@dataclass
class Config:
    bot_token: str
    base_url: str
    offline_fixtures_dir: str | None
    database_url: str
    refresh_interval_hours: int
    refresh_reconcile_minutes: int
    refresh_jitter_seconds: int
    scan_interval_seconds: int
    default_notify_offset_min: int
    # Retention / cleanup
    event_retention_days: int
    notification_retention_days: int
    cleanup_at_hh: int
    cleanup_at_mm: int
    # TZ & proxy
    tz: str
    http_proxy: str | None
    # Lesson times
    lesson_times: dict[int, tuple[str, str]] = None

    @staticmethod
    def load() -> "Config":
        lt = {
            1: ("08:00", "09:20"),
            2: ("09:35", "10:55"),
            3: ("11:10", "12:30"),
            4: ("12:45", "14:05"),
            5: ("14:20", "15:40"),
            6: ("15:55", "17:15"),
            7: ("17:30", "18:50"),
            8: ("19:00", "20:20"),
        }
        return Config(
            bot_token=os.getenv("BOT_TOKEN", ""),
            base_url=os.getenv("BASE_URL", "http://193.189.127.179:5010").rstrip("/"),
            offline_fixtures_dir=os.getenv("OFFLINE_FIXTURES_DIR") or None,
            database_url=os.getenv("DATABASE_URL", "sqlite+aiosqlite:///./bot.db"),
            refresh_interval_hours=int(os.getenv("REFRESH_INTERVAL_HOURS", "6")),
            refresh_reconcile_minutes=int(os.getenv("REFRESH_RECONCILE_MINUTES", "15")),
            refresh_jitter_seconds=int(os.getenv("REFRESH_JITTER_SECONDS", "60")),
            scan_interval_seconds=int(os.getenv("SCAN_INTERVAL_SECONDS", "60")),
            default_notify_offset_min=int(os.getenv("DEFAULT_NOTIFY_OFFSET_MIN", "5")),
            # Retention
            event_retention_days=int(os.getenv("EVENT_RETENTION_DAYS", "90")),
            notification_retention_days=int(os.getenv("NOTIFICATION_RETENTION_DAYS", "30")),
            cleanup_at_hh=int(os.getenv("CLEANUP_AT_HH", "3")),
            cleanup_at_mm=int(os.getenv("CLEANUP_AT_MM", "30")),
            # TZ & proxy
            tz=os.getenv("TZ", "Europe/Kyiv"),
            http_proxy=os.getenv("HTTP_PROXY") or None,
            lesson_times=lt,
        )
