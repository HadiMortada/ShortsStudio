"""Usage preflight for whichever AI is chosen in Settings (Claude or ChatGPT).

Both report a session (5-hour) and a weekly percentage; the same Settings → Usage limits apply.
"""
import os

from ai import NAMES, provider
from claude_usage import UsageBlocked, check_limits, usage_limits  # noqa: F401  (re-exported)


def read_usage(*, source=None, open_url=None):
    source = os.environ if source is None else source
    if provider(source) == 'chatgpt':
        from chatgpt_usage import read_usage as read
    else:
        from claude_usage import read_usage as read
    return read(source=source, open_url=open_url)


def require_usage(*, source=None, open_url=None, limits=None):
    source = os.environ if source is None else source
    if provider(source) != 'chatgpt':
        import claude_usage
        return claude_usage.require_usage(source=source, open_url=open_url, limits=limits)
    limits = usage_limits(limits)
    return check_limits(read_usage(source=source, open_url=open_url), limits, name=NAMES['chatgpt'])
