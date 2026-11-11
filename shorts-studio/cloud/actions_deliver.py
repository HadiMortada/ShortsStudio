"""Deliver a film made by actions_film.py: save it to the private library, record autopilot
history and optionally upload to YouTube.

Runs in a separate GitHub Actions job that never sees the Claude token or the generated code's
render environment. YouTube credentials come from the YOUTUBE_OAUTH_JSON secret (authorized-user
JSON with a refresh token). Each upload is at most once: a receipt is committed to
shorts-studio/ledger/<job>.json before uploading, so re-running this job never posts twice.
"""
import base64
import json
import os
import sys
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import quote, urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener, urlopen

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

LEDGER = 'shorts-studio/ledger/'


def github(method, path, data=None, *, source=None):
    source = os.environ if source is None else source
    url = ('https://api.github.com/repos/' + source['GITHUB_REPOSITORY'] + '/contents/' + quote(path)
           + ('?ref=' + quote(source['GITHUB_REF_NAME']) if method == 'GET' and source.get('GITHUB_REF_NAME') else ''))
    headers = {'Authorization': 'Bearer ' + source['GITHUB_TOKEN'], 'Accept': 'application/vnd.github+json',
               'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'ShortsStudio'}
    body = json.dumps(data).encode() if data is not None else None
    with urlopen(Request(url, data=body, method=method, headers=headers), timeout=40) as response:
        return json.loads(response.read().decode())


class RepositoryFile:
    """A JSON file stored in the repository through the contents API."""

    def __init__(self, path, api=github, branch=None):
        self.path, self.api = path, api
        self.branch = branch if branch is not None else os.environ.get('GITHUB_REF_NAME', '')
        self.sha = None

    def read(self, default=None):
        try:
            item = self.api('GET', self.path)
        except HTTPError as error:
            if error.code == 404:
                return default
            raise
        self.sha = item['sha']
        return json.loads(base64.b64decode(item['content']).decode())

    def write(self, value, message, *, create_only=False):
        payload = {'message': message, 'content': base64.b64encode(
            json.dumps(value, ensure_ascii=False, indent=1).encode()).decode()}
        if self.branch:
            payload['branch'] = self.branch
        if self.sha and not create_only:
            payload['sha'] = self.sha
        try:
            result = self.api('PUT', self.path, payload)
        except HTTPError as error:
            # Without a sha GitHub refuses to overwrite an existing file (409/422).
            if create_only and error.code in (409, 422):
                return False
            raise
        self.sha = result['content']['sha']
        return True


class LedgerStore:
    """The YouTube class's store: the token stays in memory, upload receipts go to the repository."""

    def __init__(self, token, api=github, branch=None):
        self.memory = {'youtube-token': token}
        self.api, self.branch, self.files = api, branch, {}

    def file(self, key):
        if key not in self.files:
            self.files[key] = RepositoryFile(LEDGER + key.split(':', 1)[1] + '.json', self.api, self.branch)
        return self.files[key]

    def get(self, key, default=None):
        if key.startswith('upload:'):
            return self.file(key).read(default)
        return self.memory.get(key, default)

    def __setitem__(self, key, value):
        if key.startswith('upload:'):
            self.file(key).write(value, 'Record Shorts Studio YouTube upload')
        else:
            self.memory[key] = value

    def put(self, key, value, *, skip_if_exists=False):
        if not key.startswith('upload:'):
            if skip_if_exists and key in self.memory:
                return False
            self.memory[key] = value
            return True
        return self.file(key).write(value, 'Start Shorts Studio YouTube upload', create_only=skip_if_exists)

    def pop(self, key, default=None):
        return self.memory.pop(key, default)


def summary(text):
    print(text)
    path = os.environ.get('GITHUB_STEP_SUMMARY')
    if path:
        with open(path, 'a', encoding='utf-8') as stream:
            stream.write(text + '\n')


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, *args, **kwargs):
        return None


def rest(method, url, body=None, headers=None, *, source=None):
    """GitHub REST call. Returns (status, bytes, response headers); never follows redirects, so the
    token is not forwarded to the storage host that serves release assets."""
    source = os.environ if source is None else source
    if url.startswith('/'):
        url = 'https://api.github.com' + url
    data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
    base = {'Accept': 'application/vnd.github+json', 'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'ShortsStudio'}
    if urlsplit(url).hostname in ('api.github.com', 'uploads.github.com'):
        base['Authorization'] = 'Bearer ' + source['GITHUB_TOKEN']
    if isinstance(body, (dict, list)):
        base['Content-Type'] = 'application/json'
    request = Request(url, data=data, method=method, headers={**base, **(headers or {})})
    try:
        with build_opener(NoRedirect).open(request, timeout=300) as response:
            return response.status, response.read(), dict(response.headers)
    except HTTPError as error:
        if error.code in (301, 302, 303, 307, 308):
            return error.code, b'', dict(error.headers)
        raise


class Library:
    """Finished films live as assets of one DRAFT release: invisible to the public even in a
    public repository, readable by the owner's token. library.json indexes them for the web page."""
    NAME, TAG, KEEP = 'Shorts Studio films', 'shorts-studio-films', 40

    def __init__(self, repo, call=rest):
        self.repo, self.call = repo, call
        self.release = None

    def json(self, method, path, body=None):
        status, data, _ = self.call(method, '/repos/' + self.repo + path, body)
        return json.loads(data) if data else None

    def ensure(self):
        if self.release is None:
            releases = self.json('GET', '/releases?per_page=100') or []
            found = [r for r in releases if r.get('draft') and r.get('name') == self.NAME]
            self.release = found[0] if found else self.json('POST', '/releases', {
                'tag_name': self.TAG, 'name': self.NAME, 'draft': True,
                'body': 'Private Shorts Studio film library (draft release, visible only to collaborators).'})
        return self.release

    def assets(self):
        return self.json('GET', f'/releases/{self.ensure()["id"]}/assets?per_page=100') or []

    def delete(self, asset):
        self.call('DELETE', f'/repos/{self.repo}/releases/assets/{asset["id"]}')

    def upload(self, name, content, content_type):
        for asset in self.assets():
            if asset['name'] == name:
                self.delete(asset)
        url = f'https://uploads.github.com/repos/{self.repo}/releases/{self.ensure()["id"]}/assets?name={quote(name)}'
        _, data, _ = self.call('POST', url, content, {'Content-Type': content_type})
        return json.loads(data)['id']

    def download(self, asset):
        status, data, headers = self.call('GET', f'/repos/{self.repo}/releases/assets/{asset["id"]}',
                                          None, {'Accept': 'application/octet-stream'})
        if status in (301, 302, 303, 307, 308):
            _, data, _ = self.call('GET', headers['Location'] if 'Location' in headers else headers['location'])
        return data

    def index(self):
        for asset in self.assets():
            if asset['name'] == 'library.json':
                try:
                    films = json.loads(self.download(asset))
                    return films if isinstance(films, list) else []
                except ValueError:
                    return []
        return []

    def add(self, entry, folder, run):
        entry['video'] = self.upload(f'film-{run}.mp4', (folder / 'video.mp4').read_bytes(), 'video/mp4')
        if (folder / 'video.png').is_file():
            entry['poster'] = self.upload(f'film-{run}.png', (folder / 'video.png').read_bytes(), 'image/png')
        return entry

    def update(self, run, patch):
        """Change one film's index entry in place (keeps the library order)."""
        films = self.index()
        for film in films:
            if str(film.get('run')) == str(run):
                film.update(patch)
        self.upload('library.json', json.dumps(films, ensure_ascii=False).encode(), 'application/json')
        return films

    def video(self, run):
        for asset in self.assets():
            if asset['name'] == f'film-{run}.mp4':
                return self.download(asset)
        return None

    def save(self, entry):
        films = [entry] + [f for f in self.index() if f.get('run') != entry['run']]
        keep, old = films[:self.KEEP], films[self.KEEP:]
        stale = {f'film-{f.get("run")}.mp4' for f in old} | {f'film-{f.get("run")}.png' for f in old}
        for asset in self.assets():
            if asset['name'] in stale:
                self.delete(asset)
        self.upload('library.json', json.dumps(keep, ensure_ascii=False).encode(), 'application/json')
        return keep


def deliver(folder, *, api=github, youtube_factory=None, source=None, library=None):
    source = os.environ if source is None else source
    folder = Path(folder)
    delivery = json.loads((folder / 'delivery.json').read_text())
    if delivery.get('state') != 'ready':
        summary('Nothing to deliver: the film is not ready.')
        return {'uploaded': False}
    result = {'uploaded': False}
    if delivery.get('autonomous') and (folder / 'history.json').is_file():
        history = json.loads((folder / 'history.json').read_text())
        target = RepositoryFile('shorts-studio/autopilot/history.json', api)
        target.read([])
        target.write(history[-90:], 'Remember Shorts Studio autopilot topic')
        result['history'] = len(history)
    if delivery.get('ideaId'):
        # The idea was used: take it off the Up next list.
        for attempt in range(2):
            ideas = RepositoryFile('shorts-studio/autopilot/ideas.json', api)
            current = ideas.read([]) or []
            kept = [x for x in current if x.get('id') != delivery['ideaId']]
            if len(kept) == len(current):
                break
            try:
                ideas.write(kept, 'Use a topic idea from the Up next list')
                break
            except HTTPError:
                if attempt:
                    raise
    run = str(source.get('GITHUB_RUN_ID') or delivery['jobId'])
    design_file = folder / 'design.json'
    design_data = json.loads(design_file.read_text()) if design_file.is_file() else {}
    credits = design_data.get('sources', [])[:20]
    from youtube import youtube_pack
    suggested = youtube_pack(design_data)['hashtags'][1:] if design_data else []
    entry = {'run': run, 'sources': credits, 'youtubePack': design_data.get('youtube') or {}, 'hashtags': suggested,
             'language': design_data.get('language', 'en'), **{k: delivery.get(k) for k in ('jobId', 'title', 'description', 'writer', 'writerDetail',
             'fallbackReason', 'claudeIssue', 'seconds', 'createdAt', 'brief', 'autonomous', 'slot')}}
    library = library if library is not None else (Library(source['GITHUB_REPOSITORY']) if source.get('GITHUB_REPOSITORY') else None)
    if library is not None:
        library.add(entry, folder, run)
    error = None
    try:
        if not delivery.get('publish'):
            if delivery.get('heldReason'):
                summary('YouTube: ' + delivery['heldReason'])
        elif not delivery.get('qualityPassed'):
            summary('YouTube upload held: the visual review did not pass. Preview the film and publish manually.')
            entry['uploadError'] = 'Held after the visual review'
        else:
            raw = source.get('YOUTUBE_OAUTH_JSON', '').strip()
            if not raw:
                raise RuntimeError('Add the YOUTUBE_OAUTH_JSON repository secret before publishing.')
            from youtube import YouTube, channel_hashtags
            store = LedgerStore(json.loads(raw), api)
            extra = channel_hashtags(source.get('STUDIO_SETTINGS', 'shorts-studio/autopilot/settings.json'))
            youtube = (youtube_factory or YouTube)(store)
            design = json.loads((folder / 'design.json').read_text())
            if delivery.get('publishAt'):
                receipt = youtube.upload(delivery['jobId'], folder / 'video.mp4', design, delivery['privacy'],
                                         publish_at=delivery['publishAt'], hashtags=extra)
                summary('Scheduled on YouTube for ' + delivery['publishAt'] + ': ' + receipt.get('url', ''))
                entry['scheduledFor'] = delivery['publishAt']
            else:
                receipt = youtube.upload(delivery['jobId'], folder / 'video.mp4', design, delivery['privacy'], hashtags=extra)
                summary('Published to YouTube (' + delivery['privacy'] + '): ' + receipt.get('url', ''))
            entry['youtube'] = receipt.get('url', '')
            result.update(uploaded=True, receipt=receipt)
    except Exception as failure:
        entry['uploadError'] = str(failure)[:300]
        error = failure
    if library is not None:
        library.save(entry)
        summary('Saved to your Shorts Studio library.')
    if error:
        raise error
    return result


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: actions_deliver.py FILM_FOLDER')
    print(json.dumps(deliver(sys.argv[1])))


if __name__ == '__main__':
    main()
