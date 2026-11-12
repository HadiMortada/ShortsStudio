"""Persistent, opt-in cloud scheduling. Each local calendar slot is claimed once."""
import hashlib
import json
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Schedule(BaseModel):
    model_config = ConfigDict(extra='forbid')
    enabled: bool = False
    domain: str = Field(default='', max_length=2000)
    audience: str = Field(default='General audience', max_length=400)
    language: str = Field(default='Arabic', max_length=80)
    tone: str = Field(default='Clear, engaging and factual', max_length=400)
    time: str = '18:00'
    # GitHub Actions mode: every local time a film should be posted on the selected days.
    times: list[str] = Field(default_factory=lambda: ['18:00'], min_length=1, max_length=6)
    timezone: str = 'Asia/Beirut'
    days: list[int] = Field(default_factory=lambda: list(range(7)), min_length=1, max_length=7)
    seconds: int = Field(default=30, ge=15, le=60)
    voice: str = 'auto'
    resources: list[str] = Field(default_factory=list, max_length=5)
    publish: bool = False
    # GitHub Actions mode: also upload films made by the free backup writer.
    publishBackup: bool = False
    privacy: str = 'private'
    # GitHub Actions mode: auto = Claude then the free backup, claude = Claude only, backup = backup only.
    writer: str = 'auto'
    # Burned-in, word-by-word captions on every film.
    captions: bool = True
    # A short "Like & Subscribe" card after the last word of every film.
    endCard: bool = True
    # The owner's hashtags, added to every YouTube post.
    hashtags: list[str] = Field(default_factory=list, max_length=8)

    @field_validator('time')
    @classmethod
    def clock(cls, value):
        if not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', value):
            raise ValueError('Use HH:mm')
        return value

    @field_validator('times')
    @classmethod
    def clocks(cls, value):
        if any(not re.fullmatch(r'(?:[01]\d|2[0-3]):[0-5]\d', x) for x in value) or len(set(value)) != len(value):
            raise ValueError('Use unique HH:mm post times')
        return sorted(value)

    @field_validator('timezone')
    @classmethod
    def zone(cls, value):
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError):
            raise ValueError('Select a valid IANA timezone') from None
        return value

    @field_validator('days')
    @classmethod
    def weekdays(cls, value):
        if any(x < 0 or x > 6 for x in value) or len(set(value)) != len(value):
            raise ValueError('Days must be unique, Monday=0 through Sunday=6')
        return value

    @field_validator('writer')
    @classmethod
    def writer_mode(cls, value):
        if value not in ('auto', 'claude', 'backup'):
            raise ValueError('Choose auto, claude or backup')
        return value

    @field_validator('privacy')
    @classmethod
    def visibility(cls, value):
        if value not in ('private', 'unlisted', 'public'):
            raise ValueError('Invalid YouTube visibility')
        return value


class FileStore:
    """Local development only; deployed instances use a shared Modal Dict."""
    def __init__(self, root):
        self.root = Path(root) / 'automation'
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def path(self, key):
        return self.root / (hashlib.sha256(key.encode()).hexdigest() + '.json')

    def get(self, key, default=None):
        try:
            return json.loads(self.path(key).read_text())
        except FileNotFoundError:
            return default

    def __setitem__(self, key, value):
        path = self.path(key)
        tmp = path.with_suffix('.tmp')
        tmp.write_text(json.dumps(value, ensure_ascii=False))
        tmp.chmod(0o600)
        tmp.replace(path)

    def pop(self, key, default=None):
        value = self.get(key, default)
        self.path(key).unlink(missing_ok=True)
        return value

    def put(self, key, value, *, skip_if_exists=False):
        if not skip_if_exists:
            self[key] = value
            return True
        try:
            with self.path(key).open('x') as stream:
                json.dump(value, stream)
            self.path(key).chmod(0o600)
            return True
        except FileExistsError:
            return False


class Automation:
    def __init__(self, store):
        self.store = store

    def config(self):
        return self.store.get('schedule', {'settings': Schedule().model_dump(), 'savedAt': 0})

    def save(self, config, now=None):
        from production import VOICES
        value = Schedule.model_validate(config)
        if value.enabled and not value.domain.strip():
            raise ValueError('Enter the content domain first')
        if value.voice not in VOICES:
            raise ValueError('Select a supported narrator')
        result = {'settings': value.model_dump(), 'savedAt': now if now is not None else time.time()}
        self.store['schedule'] = result
        return result

    def history(self):
        return self.store.get('history', [])

    def remember(self, item):
        entries = self.history()
        entries = [x for x in entries if x.get('jobId') != item.get('jobId')]
        self.store['history'] = (entries + [item])[-90:]

    def tick(self, production, now=None):
        # Touch durable Dict entries even while disabled (Modal expires idle keys).
        saved = self.config()
        self.history()
        self.store.get('youtube-client')
        self.store.get('youtube-token')
        config = Schedule.model_validate(saved['settings'])
        if not config.enabled:
            return {'state': 'disabled'}
        moment = datetime.fromtimestamp(now if now is not None else time.time(), timezone.utc).astimezone(ZoneInfo(config.timezone))
        h, m = map(int, config.time.split(':'))
        due = moment.replace(hour=h, minute=m, second=0, microsecond=0)
        if moment.weekday() not in config.days or moment < due or due.timestamp() < saved['savedAt']:
            return {'state': 'waiting'}
        # Saving/editing a schedule never creates a second run on the same local day.
        slot = 'daily:' + moment.date().isoformat()
        previous = self.store.get(slot)
        if previous:
            return previous
        if production.active:
            return {'state': 'busy'}
        receipt = {'state': 'starting', 'date': moment.date().isoformat(), 'startedAt': time.time()}
        if not self.store.put(slot, receipt, skip_if_exists=True):
            return self.store.get(slot)
        try:
            options = {'research': 'auto', 'resources': config.resources,
                       'autonomous': config.model_dump(), 'publish': config.publish,
                       'privacy': config.privacy, 'slot': slot}
            job = production.start(config.domain, config.seconds, config.voice, options=options)
            receipt.update(state='queued', jobId=job['id'])
        except Exception:
            receipt.update(state='failed', message='Could not dispatch the daily film. Check the cloud connection.')
        self.store[slot] = receipt
        self.store['last-run'] = receipt
        return receipt
