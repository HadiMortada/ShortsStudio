"""Preserve a film's original request, without credentials, across failed runners."""
import json
import os
from pathlib import Path

FIELDS = ('brief', 'seconds', 'voice', 'writer', 'research', 'publish', 'privacy', 'slot', 'instagram')


def clean_request(value):
    if not isinstance(value, dict):
        raise ValueError('Invalid saved film request')
    result = {}
    defaults = dict(brief='', seconds=30, voice='auto', writer='auto', research='auto',
                    publish=False, privacy='private', slot='', instagram='')
    for key in FIELDS:
        result[key] = value.get(key, defaults[key])
    if isinstance(result['seconds'], bool) or not (isinstance(result['seconds'], int) or
            isinstance(result['seconds'], str) and result['seconds'].isdigit()):
        raise ValueError('Invalid saved film length')
    result['seconds'] = int(result['seconds'])
    if not 15 <= result['seconds'] <= 60:
        raise ValueError('Invalid saved film length')
    if result['publish'] in ('true', 'false'):
        result['publish'] = result['publish'] == 'true'
    if not isinstance(result['publish'], bool):
        raise ValueError('Invalid saved publication choice')
    for key in FIELDS:
        if key not in ('seconds', 'publish') and (not isinstance(result[key], str) or len(result[key]) > 6000):
            raise ValueError('Invalid saved film request')
    if result['writer'] not in ('auto', 'claude', 'backup') or result['research'] not in ('auto', 'off') or result['privacy'] not in ('private', 'unlisted', 'public'):
        raise ValueError('Invalid saved film options')
    return result


def previous_request(folder):
    path = Path(folder) / 'request.json'
    if not path.is_file():
        return None  # Older runs did not save a request. The phone supplies it.
    if path.stat().st_size > 50000:
        raise ValueError('Saved film request is too large')
    return clean_request(json.loads(path.read_text(encoding='utf-8')))


def restore_request(args):
    if not getattr(args, 'resume', False):
        return
    saved = previous_request(args.previous) or previous_request(args.out)
    if saved:
        for key, value in saved.items():
            setattr(args, key, value)


def save_request(folder, value):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    (folder / 'request.json').write_text(json.dumps(clean_request(value), ensure_ascii=False), encoding='utf-8')


def capture_request(inputs, previous):
    saved = previous_request(previous) if inputs.get('resume_run') else None
    value = saved or inputs
    if inputs.get('resume_run') and not saved and not value.get('brief') and not value.get('slot'):
        # Never silently turn a legacy manual continuation into an autopilot film.
        try:
            design = json.loads((Path(previous) / 'design.json').read_text(encoding='utf-8'))
            title = design.get('title')
            if not isinstance(title, str) or not title.strip():
                raise ValueError()
            value = {**value, 'brief': title[:4000]}
        except (OSError, ValueError, AttributeError):
            raise ValueError('Original brief unavailable. Enter it on New film to try again.') from None
    return clean_request(value)


if __name__ == '__main__':
    import sys
    event = json.loads(Path(os.environ['GITHUB_EVENT_PATH']).read_text())
    inputs = event.get('inputs') or {}
    save_request(sys.argv[1], capture_request(inputs, sys.argv[2]))
