"""Autopilot post times: which slot is due now, and whether it was already handled."""
from datetime import datetime, time as clock, timedelta, timezone
from zoneinfo import ZoneInfo

LEAD = timedelta(minutes=65)    # start making a film this long before its post time (checks run every 15 min)
GRACE = timedelta(minutes=150)  # still make it this long after (late runner); it is then posted at once


def slot_label(slot):
    return slot.isoformat(timespec='minutes')


def parse_slot(label):
    return datetime.fromisoformat(label)


def claimed(history):
    return {x.get('slot') for x in history if isinstance(x, dict) and x.get('slot')}


def due_slot(settings, history, now=None, busy=()):
    """The earliest post time whose making window contains `now` and that has not been made yet.
    `busy` holds slot labels already queued or running."""
    zone = ZoneInfo(settings.get('timezone') or 'UTC')
    local = (now or datetime.now(timezone.utc)).astimezone(zone)
    done = claimed(history) | set(busy)
    for offset in (-1, 0, 1):
        day = (local + timedelta(days=offset)).date()
        if day.weekday() not in settings.get('days', range(7)):
            continue
        for value in sorted(settings.get('times') or ['18:00']):
            h, m = map(int, value.split(':'))
            slot = datetime.combine(day, clock(h, m), tzinfo=zone)
            if slot - LEAD <= local <= slot + GRACE and slot_label(slot) not in done:
                return slot
    return None


def publish_at(slot_text, now=None, margin=timedelta(minutes=10)):
    """YouTube's scheduled publish time (UTC), or None when the slot is too close or already past."""
    if not slot_text:
        return None
    slot = parse_slot(slot_text)
    if slot <= (now or datetime.now(timezone.utc)) + margin:
        return None
    return slot.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
