"""Autopilot post times: which slot is due now, and whether it was already handled."""
from datetime import datetime, time as clock, timedelta, timezone
from zoneinfo import ZoneInfo
import re

LEAD = timedelta(minutes=65)    # start making a film this long before its post time (checks run every 15 min)
GRACE = timedelta(minutes=150)  # still make it this long after (late runner); it is then posted at once


# Autopilot topics: each has its own subject, language, voice, length, days, post times and accounts;
# everything else (timezone, writer, captions, hashtags, never-list) is shared. A post time of a
# topic is labelled "<ISO time>#<topic id>", so every film knows which topic it belongs to.
TOPIC_KEYS = ('domain', 'audience', 'language', 'tone', 'seconds', 'voice', 'days', 'times', 'publish', 'privacy', 'instagram')
TOPIC_ID = re.compile(r'[a-z0-9]{1,12}')


def topics_of(settings):
    """[(topic id, settings for that topic)]; settings without topics are one topic with id ''."""
    topics = settings.get('topics') if isinstance(settings, dict) else None
    base = {k: v for k, v in (settings or {}).items() if k != 'topics'}
    out = []
    for topic in topics if isinstance(topics, list) else []:
        if isinstance(topic, dict) and TOPIC_ID.fullmatch(str(topic.get('id', ''))):
            out.append((topic['id'], {**base, **{k: topic[k] for k in TOPIC_KEYS if k in topic}, 'name': topic.get('name', '')}))
    return out or [('', base)]


def topic_settings(settings, topic_id):
    """The settings for one topic, or None when it no longer exists."""
    for tid, merged in topics_of(settings):
        if tid == (topic_id or '') or (not topic_id and tid == topics_of(settings)[0][0]):
            return merged
    return None


def split_slot(text):
    """'2026-11-14T09:00+02:00#science' → ('2026-11-14T09:00+02:00', 'science')."""
    label, _, topic = (text or '').partition('#')
    return label, topic


def slot_label(slot):
    return slot.isoformat(timespec='minutes')


def parse_slot(label):
    return datetime.fromisoformat(split_slot(label)[0])


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


def due_label(settings, history, now=None, busy=()):
    """The label of the post time (of any topic) that is due now, or None."""
    found = []
    for tid, merged in topics_of(settings):
        if not str(merged.get('domain', '')).strip():
            continue
        suffix = '#' + tid if tid else ''
        done = {split_slot(x)[0] for x in (claimed(history) | set(busy)) if split_slot(x)[1] == tid}
        slot = due_slot({**merged}, [], now, done)
        if slot:
            found.append((slot, slot_label(slot) + suffix))
    return min(found)[1] if found else None


def publish_at(slot_text, now=None, margin=timedelta(minutes=10)):
    """YouTube's scheduled publish time (UTC), or None when the slot is too close or already past."""
    if not slot_text:
        return None
    slot = parse_slot(slot_text)
    if slot <= (now or datetime.now(timezone.utc)) + margin:
        return None
    return slot.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
