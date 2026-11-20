"""ChatGPT subscription usage (Codex limits) before every film. Never inferred from token counts.

Reads the same endpoint the Codex CLI's /status uses. It is not a versioned public API: missing
fields, an expired sign-in and network errors stop creation, never allow it. Plus and Pro plans
report a 5-hour window and a weekly window; plans with only a weekly window report 0% session.
"""
import json
import math
import os
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener

from claude_usage import NoRedirect, UsageBlocked

USAGE_URL = 'https://chatgpt.com/backend-api/wham/usage'
SESSION_WINDOW = 5 * 3600


def credentials(source):
    from chatgpt_account import AccountUnavailable, access_token, account_credentials
    try:
        access = access_token(source)
    except AccountUnavailable as error:
        raise UsageBlocked(str(error)) from None
    if access:
        return access
    home = Path(source.get('CODEX_HOME') or Path(source.get('HOME', str(Path.home()))) / '.codex')
    try:
        return account_credentials((home / 'auth.json').read_text(encoding='utf-8'))
    except (OSError, AccountUnavailable):
        pass
    raise UsageBlocked('Video creation paused: sign in to ChatGPT so session and weekly usage can be checked.')


def _iso(seconds):
    if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not math.isfinite(seconds) or seconds <= 0:
        return None
    try:
        return datetime.fromtimestamp(seconds, timezone.utc).strftime('%Y-%m-%dT%H:%M:%SZ')
    except (OverflowError, OSError, ValueError):
        return None


def validate_usage(data):
    """{'sessionPercent', 'weeklyPercent', 'sessionResetsAt', 'weeklyResetsAt'} from the response."""
    limits = data.get('rate_limit') if isinstance(data, dict) else None
    windows = [limits.get(k) for k in ('primary_window', 'secondary_window')] if isinstance(limits, dict) else []
    usage = {}
    for window in windows:
        if not isinstance(window, dict):
            continue
        value, length = window.get('used_percent'), window.get('limit_window_seconds')
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
            continue
        label = 'session' if isinstance(length, (int, float)) and not isinstance(length, bool) and 0 < length <= 86400 else 'weekly'
        usage[label + 'Percent'] = value
        reset = _iso(window.get('reset_at'))
        if reset:
            usage[label + 'ResetsAt'] = reset
    if 'weeklyPercent' not in usage and 'sessionPercent' not in usage:
        raise UsageBlocked('Video creation paused: ChatGPT did not return valid session and weekly usage. Try again later.')
    # A plan without one of the windows has no limit there.
    usage.setdefault('sessionPercent', 0)
    usage.setdefault('weeklyPercent', 0)
    return usage


def read_usage(*, source=None, open_url=None):
    """One uncached, read-only request immediately before a film starts; no model call."""
    source = os.environ if source is None else source
    access = credentials(source)
    request = Request(USAGE_URL, headers={'Authorization': 'Bearer ' + access['accessToken'],
                      'ChatGPT-Account-Id': access['accountId'], 'Accept': 'application/json',
                      'User-Agent': 'ShortsStudio'})
    open_url = open_url or build_opener(NoRedirect()).open
    try:
        with open_url(request, timeout=20) as response:
            data = response.read(65537)
        if len(data) > 65536:
            raise ValueError('Oversized response')
        return validate_usage(json.loads(data))
    except UsageBlocked:
        raise
    except HTTPError as error:
        if error.code in (401, 403):
            message = ('This ChatGPT sign-in cannot read account usage. Open Account in Shorts Studio '
                       'and tap Reconnect ChatGPT.')
        elif error.code == 429:
            message = 'ChatGPT usage checking is temporarily rate limited. Try again later.'
        else:
            message = 'ChatGPT usage checking is unavailable. Try again later.'
    except (OSError, ValueError, TypeError):
        message = 'ChatGPT usage could not be verified. Try again later.'
    raise UsageBlocked('Video creation paused: ' + message) from None
