"""Fresh subscription-usage preflight. Never infer plan usage from token counts.

Claude Code's OAuth usage endpoint is not a versioned public API. Missing fields,
insufficient token scopes and network errors therefore stop creation, never allow it.
"""
import json
import math
import os
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import Request, build_opener, HTTPRedirectHandler

USAGE_URL = 'https://api.anthropic.com/api/oauth/usage'


class UsageBlocked(RuntimeError):
    def __init__(self, message, usage=None):
        super().__init__(message)
        self.usage = usage or {}


class NoRedirect(HTTPRedirectHandler):
    # A bearer token must never be forwarded to another host.
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def subscription_token(source):
    from claude_account import access_token, AccountUnavailable
    try:
        token = access_token(source)
    except AccountUnavailable as error:
        raise UsageBlocked(str(error)) from None
    if token:
        return token
    config = Path(source.get('CLAUDE_CONFIG_DIR') or Path(source.get('HOME', str(Path.home()))) / '.claude')
    try:
        data = json.loads((config / '.credentials.json').read_text(encoding='utf-8'))
        token = data['claudeAiOauth']['accessToken']
        if isinstance(token, str) and token.strip():
            return token.strip()
    except (OSError, ValueError, KeyError, TypeError):
        pass
    raise UsageBlocked('Video creation paused: sign in to Claude so session and weekly usage can be checked.')


def validate_usage(data, *, enforce_limits=True):
    usage = {}
    for key, label in (('five_hour', 'sessionPercent'), ('seven_day', 'weeklyPercent')):
        window = data.get(key) if isinstance(data, dict) else None
        value = window.get('utilization') if isinstance(window, dict) else None
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value) or not 0 <= value <= 100:
            raise UsageBlocked('Video creation paused: Claude did not return valid session and weekly usage. Try again later.')
        usage[label] = value
        reset = window.get('resets_at')
        if isinstance(reset, str) and len(reset) <= 64:
            usage[label.replace('Percent', 'ResetsAt')] = reset
    return check_limits(usage) if enforce_limits else usage


def check_limits(usage):
    if usage['sessionPercent'] > 50 or usage['weeklyPercent'] >= 80:
        raise UsageBlocked(f"Video creation paused: Claude session usage is {usage['sessionPercent']:g}% "
                           f"and weekly usage is {usage['weeklyPercent']:g}%. "
                           'Creation requires session usage at or below 50% and weekly usage below 80%.', usage)
    return usage


def read_usage(*, source=None, open_url=None):
    """One uncached, read-only request immediately before a film starts; no model call."""
    source = os.environ if source is None else source
    token = subscription_token(source)
    request = Request(USAGE_URL, headers={'Authorization': 'Bearer ' + token,
                      'anthropic-beta': 'oauth-2025-04-20', 'Accept': 'application/json',
                      'User-Agent': 'ShortsStudio'})
    open_url = open_url or build_opener(NoRedirect()).open
    try:
        with open_url(request, timeout=20) as response:
            data = response.read(65537)
        if len(data) > 65536:
            raise ValueError('Oversized response')
        return validate_usage(json.loads(data), enforce_limits=False)
    except UsageBlocked:
        raise
    except HTTPError as error:
        if error.code in (401, 403):
            message = ('This Claude sign-in cannot read account usage. Open Account in Shorts Studio '
                       'and tap Reconnect Claude to enable usage access.')
        elif error.code == 429:
            message = 'Claude usage checking is temporarily rate limited. Try again later.'
        else:
            message = 'Claude usage checking is unavailable. Try again later.'
    except (OSError, ValueError, TypeError):
        message = 'Claude usage could not be verified. Try again later.'
    raise UsageBlocked('Video creation paused: ' + message) from None


def require_usage(*, source=None, open_url=None):
    return check_limits(read_usage(source=source, open_url=open_url))
