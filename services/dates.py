"""App displays deadlines in India Standard Time; DB stores UTC datetimes."""
from datetime import datetime, timezone, timedelta

# IST is a fixed UTC+05:30 offset (no DST); works on Windows without tzdata.
LOCAL_ZONE = timezone(timedelta(hours=5, minutes=30), name='IST')


def to_utc_naive(value: datetime):
    if value.tzinfo is None:
        value = value.replace(tzinfo=LOCAL_ZONE)
    return value.astimezone(timezone.utc).replace(tzinfo=None)


def utc_now_naive():
    return datetime.now(timezone.utc).replace(tzinfo=None)


def as_local(value):
    if value is None:
        return None
    if isinstance(value, str):
        value = datetime.fromisoformat(value)
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(LOCAL_ZONE)


def pretty_deadline(value):
    return as_local(value).strftime('%d %b %Y, %I:%M %p IST') if value else 'Not set (legacy assignment)'


def is_overdue(value):
    return bool(value and value < utc_now_naive())
