from datetime import datetime, date, time, timezone
from zoneinfo import ZoneInfo
import os

_TZ = ZoneInfo(os.getenv("TZ", "Europe/Kyiv"))

def now_kiev() -> datetime:
    return datetime.now(tz=_TZ)

def today_kiev() -> datetime:
    return now_kiev().replace(hour=0, minute=0, second=0, microsecond=0)

def combine_local(d: date, t: time) -> datetime:
    return datetime(d.year, d.month, d.day, t.hour, t.minute, tzinfo=_TZ)

def to_utc(dt_local: datetime) -> datetime:
    return dt_local.astimezone(timezone.utc)
