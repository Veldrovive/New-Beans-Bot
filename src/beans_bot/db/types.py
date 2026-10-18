import datetime

from sqlalchemy import DateTime
from sqlalchemy.types import TypeDecorator


def utcnow() -> datetime.datetime:
    return datetime.datetime.now(datetime.UTC)


class UTCDateTime(TypeDecorator):
    """
    Stores timezone-aware datetimes as naive UTC (SQLite has no timezone support)
    and hands them back as aware UTC datetimes.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime.datetime | None, dialect):
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"Refusing to store naive datetime {value!r}; use an aware datetime.")
        return value.astimezone(datetime.UTC).replace(tzinfo=None)

    def process_result_value(self, value: datetime.datetime | None, dialect):
        if value is None:
            return None
        return value.replace(tzinfo=datetime.UTC)
