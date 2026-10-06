from datetime import UTC, datetime


def utcnow() -> datetime:
    return datetime.now(UTC)


def aware(dt: datetime) -> datetime:
    """SQLite возвращает время без зоны; в БД всё хранится в UTC."""
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
