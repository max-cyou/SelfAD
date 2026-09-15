from datetime import datetime, timezone

from selfad.models import InstanceConfig


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def contest_has_started(
    config: InstanceConfig | None,
    *,
    now: datetime | None = None,
) -> bool:
    if config is None:
        return False
    if config.contest_started:
        return True
    if config.contest_starts_at is None:
        return False
    current_time = as_utc(now or datetime.now(timezone.utc))
    return current_time >= as_utc(config.contest_starts_at)


def start_contest_if_due(
    config: InstanceConfig | None,
    *,
    now: datetime | None = None,
) -> bool:
    if config is None or config.contest_started:
        return False
    if not contest_has_started(config, now=now):
        return False
    config.contest_started = True
    return True
