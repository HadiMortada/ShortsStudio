"""Hourly autopilot check: start the film for the next post time, once.

Runs in its own light workflow so the film workflow's run list only shows real films. A due slot
(see slots.py) is skipped when it is already in the history or a film run for it is queued,
running or finished successfully.
"""
import json
import os
import sys
from pathlib import Path
from urllib.request import Request, urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from slots import due_slot, slot_label  # noqa: E402

FILM_WORKFLOW = 'shorts-studio-claude.yml'
IDEAS_WORKFLOW = 'shorts-studio-ideas.yml'


def api(method, path, body=None, *, source=None):
    source = os.environ if source is None else source
    request = Request('https://api.github.com/repos/' + source['GITHUB_REPOSITORY'] + path, method=method,
                      data=json.dumps(body).encode() if body is not None else None,
                      headers={'Authorization': 'Bearer ' + source['GITHUB_TOKEN'], 'Accept': 'application/vnd.github+json',
                               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'ShortsStudio',
                               'Content-Type': 'application/json'})
    with urlopen(request, timeout=30) as response:
        data = response.read()
        return json.loads(data) if data else None


def busy_slots(runs):
    prefix = 'Autopilot film '
    return {r['display_title'][len(prefix):] for r in runs
            if r.get('display_title', '').startswith(prefix) and (r.get('status') != 'completed' or r.get('conclusion') == 'success')}


def refill_ideas(ideas_path, call, branch, now_ts):
    """Ask for new topic ideas when fewer than 3 are left, at most every 6 hours."""
    try:
        ideas = json.loads(Path(ideas_path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        ideas = []
    if isinstance(ideas, list) and len(ideas) >= 3:
        return False
    from datetime import datetime
    runs = (call('GET', f'/actions/workflows/{IDEAS_WORKFLOW}/runs?per_page=5') or {}).get('workflow_runs', [])
    for run in runs:
        started = datetime.fromisoformat(run.get('created_at', '1970-01-01T00:00:00Z').replace('Z', '+00:00')).timestamp()
        if now_ts - started < 6 * 3600 and run.get('conclusion') != 'failure':
            return False
    call('POST', f'/actions/workflows/{IDEAS_WORKFLOW}/dispatches', {'ref': branch, 'inputs': {'request_id': 'autopilot'}})
    return True


def check(settings_path, history_path, *, call=api, now=None, branch='main', ideas_path=None):
    from autonomy import Schedule
    try:
        settings = Schedule.model_validate(json.loads(Path(settings_path).read_text(encoding='utf-8'))).model_dump()
    except FileNotFoundError:
        return 'No autopilot settings.'
    if not settings['enabled']:
        return 'Autopilot is off.'
    if not settings['domain'].strip():
        return 'Autopilot has no subject area.'
    try:
        history = json.loads(Path(history_path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        history = []
    import time as _time
    refilled = refill_ideas(ideas_path or Path(settings_path).with_name('ideas.json'), call, branch,
                            (now.timestamp() if now else _time.time()))
    runs = (call('GET', f'/actions/workflows/{FILM_WORKFLOW}/runs?per_page=30') or {}).get('workflow_runs', [])
    slot = due_slot(settings, history, now, busy_slots(runs))
    if not slot:
        return 'No post time is due.' + (' Asked for new topic ideas.' if refilled else '')
    label = slot_label(slot)
    call('POST', f'/actions/workflows/{FILM_WORKFLOW}/dispatches', {'ref': branch, 'inputs': {'brief': '', 'slot': label}})
    return 'Started the film for ' + label


def main():
    from notify import notify
    message = check('shorts-studio/autopilot/settings.json', 'shorts-studio/autopilot/history.json',
                    branch=os.environ.get('GITHUB_REF_NAME', 'main'))
    print(message)
    # A heartbeat lets the page show that the hourly check is running.
    notify('heartbeat', 'Hourly check', message, push=False)
    if message.startswith('Started the film for'):
        notify('autopilot', 'Autopilot started a film', 'For the ' + message.rsplit('T', 1)[-1][:5] + ' post time.', '/#create', push=False)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as stream:
            stream.write(message + '\n')


if __name__ == '__main__':
    main()
