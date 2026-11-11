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


def check(settings_path, history_path, *, call=api, now=None, branch='main'):
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
    runs = (call('GET', f'/actions/workflows/{FILM_WORKFLOW}/runs?per_page=30') or {}).get('workflow_runs', [])
    slot = due_slot(settings, history, now, busy_slots(runs))
    if not slot:
        return 'No post time is due.'
    label = slot_label(slot)
    call('POST', f'/actions/workflows/{FILM_WORKFLOW}/dispatches', {'ref': branch, 'inputs': {'brief': '', 'slot': label}})
    return 'Started the film for ' + label


def main():
    message = check('shorts-studio/autopilot/settings.json', 'shorts-studio/autopilot/history.json',
                    branch=os.environ.get('GITHUB_REF_NAME', 'main'))
    print(message)
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as stream:
            stream.write(message + '\n')


if __name__ == '__main__':
    main()
