"""Send only sanitized usage readings to the signed-in Studio page, never the token."""
import json
import time
from urllib.request import Request, urlopen

from claude_usage import UsageBlocked, read_usage
from notify import page_settings


def report_usage(usage=None, message='', *, source=None, opener=urlopen):
    page, key = page_settings(source)
    if not page:
        return False
    usage = usage or {}
    fields = ('sessionPercent', 'weeklyPercent', 'sessionResetsAt', 'weeklyResetsAt')
    body = {k: usage[k] for k in fields if k in usage}
    body.update(state='error' if message else 'ready', message=message[:400], checkedAt=int(time.time()))
    try:
        request = Request(page + '/api/claude-usage/report', data=json.dumps(body).encode(), method='POST',
                          headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                                   'User-Agent': 'ShortsStudio'})
        with opener(request, timeout=20):
            return True
    except Exception:
        # Display telemetry must never change whether a film passes its actual preflight.
        return False


def refresh_usage():
    try:
        usage = read_usage()
    except UsageBlocked as error:
        return report_usage(message=str(error))
    return report_usage(usage)


if __name__ == '__main__':
    if not refresh_usage():
        raise SystemExit('Usage could not be reported. Open the Studio page and try Refresh again.')
