"""Publish films from the private library to YouTube, chosen on the Shorts Studio page.

Input (FILMS env): JSON list of {"run": "<run id>", "publishAt": "<UTC ISO or empty>"}; PRIVACY env.
Each film is downloaded from the draft-release library and uploaded with the same at-most-once
ledger as automatic posting, so a film is never posted twice. Results are written back to the
library index (YouTube link, scheduled time or the error).
"""
import hashlib
import json
import os
import re
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from actions_deliver import Library, LedgerStore, github, summary  # noqa: E402

ISO = re.compile(r'^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z$')


def parse_films(raw):
    films = json.loads(raw or '[]')
    if not isinstance(films, list) or not 1 <= len(films) <= 20:
        raise ValueError('Choose between 1 and 20 films')
    clean = []
    for item in films:
        run = str(item.get('run', ''))
        when = item.get('publishAt') or ''
        if not re.fullmatch(r'\d{1,20}', run) or (when and not ISO.match(when)):
            raise ValueError('Invalid film selection')
        clean.append({'run': run, 'publishAt': when})
    return clean


def publish(films, privacy, *, library, youtube, workdir):
    if privacy not in ('private', 'unlisted', 'public'):
        raise ValueError('Invalid YouTube visibility')
    index = {str(f.get('run')): f for f in library.index()}
    results = []
    for item in films:
        run, when = item['run'], item['publishAt'] or None
        entry = index.get(run)
        if not entry:
            results.append({'run': run, 'error': 'Film not found in the library'})
            continue
        patch = {}
        try:
            data = library.video(run)
            if data is None:
                raise RuntimeError('The video file is missing from the library')
            video = Path(workdir) / f'film-{run}.mp4'
            video.write_bytes(data)
            design = {'title': entry.get('title') or 'Shorts Studio film', 'description': entry.get('description') or '',
                      'sources': entry.get('sources') or []}
            job = entry.get('jobId') or hashlib.sha256(('film:' + run).encode()).hexdigest()[:32]
            receipt = youtube.upload(job, video, design, privacy, publish_at=when if privacy == 'public' else None)
            patch = {'youtube': receipt.get('url', ''), 'scheduledFor': receipt.get('scheduledFor') or '', 'uploadError': ''}
            video.unlink(missing_ok=True)
        except Exception as error:
            patch = {'uploadError': str(error)[:300]}
        library.update(run, patch)
        results.append({'run': run, **patch})
    return results


def main():
    from youtube import YouTube
    films = parse_films(os.environ.get('FILMS'))
    raw = os.environ.get('YOUTUBE_OAUTH_JSON', '').strip()
    if not raw:
        raise SystemExit('Connect YouTube on the Shorts Studio page (Account → YouTube) first.')
    library = Library(os.environ['GITHUB_REPOSITORY'])
    with tempfile.TemporaryDirectory(prefix='publish-') as workdir:
        results = publish(films, os.environ.get('PRIVACY', 'public'), library=library,
                          youtube=YouTube(LedgerStore(json.loads(raw), github)), workdir=workdir)
    for r in results:
        summary(f"Film {r['run']}: " + (r.get('uploadError') or r.get('error') or
                ('scheduled ' + r['scheduledFor'] if r.get('scheduledFor') else 'posted ' + r.get('youtube', ''))))
    if any(r.get('uploadError') or r.get('error') for r in results):
        sys.exit(1)


if __name__ == '__main__':
    main()
