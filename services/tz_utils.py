"""KST 시간 유틸리티 (maesil-total 차용)."""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def now_kst() -> datetime:
    return datetime.now(KST)


def today_kst() -> str:
    return now_kst().strftime('%Y-%m-%d')


def days_ago_kst(days: int) -> str:
    return (now_kst() - timedelta(days=days)).strftime('%Y-%m-%d')


def to_kst(dt) -> datetime:
    if dt is None:
        return None
    if isinstance(dt, str):
        try:
            dt = datetime.fromisoformat(dt.replace('Z', '+00:00'))
        except ValueError:
            return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(KST)
