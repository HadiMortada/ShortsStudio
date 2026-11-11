"""Deliver a film made by actions_film.py: record autopilot history and optionally upload to YouTube.

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
from urllib.parse import quote
from urllib.request import Request, urlopen

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


def deliver(folder, *, api=github, youtube_factory=None, source=None):
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
    if not delivery.get('publish'):
        return result
    if not delivery.get('qualityPassed'):
        summary('YouTube upload held: the visual review did not pass. Preview the MP4 artifact and publish manually.')
        return result
    raw = source.get('YOUTUBE_OAUTH_JSON', '').strip()
    if not raw:
        raise RuntimeError('Add the YOUTUBE_OAUTH_JSON repository secret before publishing.')
    from youtube import YouTube
    store = LedgerStore(json.loads(raw), api)
    youtube = (youtube_factory or YouTube)(store)
    design = json.loads((folder / 'design.json').read_text())
    receipt = youtube.upload(delivery['jobId'], folder / 'video.mp4', design, delivery['privacy'])
    summary('Published to YouTube (' + delivery['privacy'] + '): ' + receipt.get('url', ''))
    result.update(uploaded=True, receipt=receipt)
    return result


def main():
    if len(sys.argv) != 2:
        raise SystemExit('Usage: actions_deliver.py FILM_FOLDER')
    print(json.dumps(deliver(sys.argv[1])))


if __name__ == '__main__':
    main()
