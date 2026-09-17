from datetime import datetime, timezone

from selfad.models import InstanceConfig


NOT_STARTED = "not_started"
STARTED = "started"
ENDED = "ended"
CONTEST_STATES = {NOT_STARTED, STARTED, ENDED}


def as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def contest_has_started(
    config: InstanceConfig | None,
    *,
    now: datetime | None = None,
) -> bool:
    return contest_state(config, now=now) != NOT_STARTED


def contest_state(
    config: InstanceConfig | None,
    *,
    now: datetime | None = None,
) -> str:
    if config is None:
        return NOT_STARTED
    if config.contest_ended:
        return ENDED
    current_time = as_utc(now or datetime.now(timezone.utc))
    if (
        config.contest_ends_at is not None
        and current_time >= as_utc(config.contest_ends_at)
    ):
        return ENDED
    if config.contest_started:
        return STARTED
    if config.contest_starts_at is None:
        return NOT_STARTED
    return (
        STARTED
        if current_time >= as_utc(config.contest_starts_at)
        else NOT_STARTED
    )


def start_contest_if_due(
    config: InstanceConfig | None,
    *,
    now: datetime | None = None,
) -> bool:
    if config is None or config.contest_ended:
        return False
    state = contest_state(config, now=now)
    if state == ENDED:
        config.contest_started = True
        config.contest_ended = True
        return True
    if config.contest_started or state != STARTED:
        return False
    config.contest_started = True
    return True
