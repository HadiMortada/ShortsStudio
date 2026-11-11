"""Report what happened to the Shorts Studio page: it keeps the Activity timeline and sends phone
notifications. Needs STUDIO_PAGE_URL (variable) and STUDIO_NOTIFY_KEY (secret), which the page
sets up itself; without them this quietly does nothing."""
import json
import os
import sys
from urllib.request import Request, urlopen


def notify(kind, title, text='', url='/#activity', push=True, *, source=None, opener=urlopen):
    source = os.environ if source is None else source
    page, key = source.get('STUDIO_PAGE_URL', '').strip(), source.get('STUDIO_NOTIFY_KEY', '').strip()
    if page.startswith('{'):
        try:
            page = json.loads(page).get('url', '')
        except ValueError:
            page = ''
    if not page.startswith('https://') or not key:
        return False
    body = json.dumps({'kind': kind, 'title': title[:160], 'text': text[:400], 'url': url, 'push': push}).encode()
    try:
        with opener(Request(page.rstrip('/') + '/api/notify', data=body, method='POST',
                            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                                     'User-Agent': 'ShortsStudio'}), timeout=20):
            return True
    except Exception as error:  # never fail a job because a notification could not be sent
        print('Notification not sent:', type(error).__name__, file=sys.stderr)
        return False
