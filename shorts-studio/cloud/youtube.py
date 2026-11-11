"""OAuth stays outside Claude's inputs; uploads use an at-most-once receipt."""
import hashlib
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

SCOPE = 'https://www.googleapis.com/auth/youtube.upload'


class YouTube:
    def __init__(self, store):
        self.store = store

    def status(self):
        return {'configured': bool(self.store.get('youtube-client')),
                'connected': bool(self.store.get('youtube-token'))}

    def configure(self, config):
        value = config.get('web', {})
        if not value.get('client_id', '').endswith('.apps.googleusercontent.com') or not value.get('client_secret'):
            raise ValueError('Upload a Google OAuth client JSON for a Web application')
        redirects = value.get('redirect_uris', [])
        if not redirects or any(not self.valid_callback(x) for x in redirects):
            raise ValueError('Add your HTTPS Modal /youtube/callback as an authorized redirect URI')
        # Never trust token/auth endpoints from an uploaded client configuration.
        client = {'web': {'client_id': value['client_id'], 'client_secret': value['client_secret'],
            'auth_uri': 'https://accounts.google.com/o/oauth2/auth',
            'token_uri': 'https://oauth2.googleapis.com/token', 'redirect_uris': redirects}}
        self.store['youtube-client'] = client
        self.store.pop('youtube-token', None)
        return self.status()

    @staticmethod
    def valid_callback(uri):
        p = urlsplit(uri)
        return (p.scheme == 'https' and p.hostname and p.hostname.endswith('.modal.run')
                and p.port is None and not p.username and not p.password and not p.query
                and not p.fragment and p.path == '/youtube/callback')

    def begin(self, callback):
        from google_auth_oauthlib.flow import Flow
        config = self.store.get('youtube-client')
        if not config:
            raise ValueError('Configure your Google OAuth Web client first')
        if callback not in config['web']['redirect_uris']:
            raise ValueError('Add this exact authorized redirect URI to your Google OAuth client: ' + callback)
        flow = Flow.from_client_config(config, scopes=[SCOPE], autogenerate_code_verifier=True)
        flow.redirect_uri = callback
        state = secrets.token_urlsafe(32)
        url, _ = flow.authorization_url(state=state, access_type='offline', prompt='consent')
        self.store['oauth:' + hashlib.sha256(state.encode()).hexdigest()] = {
            'expires': time.time() + 600, 'verifier': flow.code_verifier, 'callback': callback}
        return {'url': url}

    def complete(self, state, code):
        from google_auth_oauthlib.flow import Flow
        if not state or len(state) > 200 or not code or len(code) > 4096:
            raise ValueError('Invalid Google authorization response')
        pending = self.store.pop('oauth:' + hashlib.sha256(state.encode()).hexdigest(), None)
        if not pending or pending['expires'] < time.time():
            raise ValueError('This connection attempt expired or was already used. Connect again.')
        flow = Flow.from_client_config(self.store.get('youtube-client'), scopes=[SCOPE],
                                       code_verifier=pending['verifier'])
        flow.redirect_uri = pending['callback']
        flow.fetch_token(code=code)
        value = json.loads(flow.credentials.to_json())
        if not value.get('refresh_token'):
            raise ValueError('Offline authorization was not granted. Reconnect with Google consent.')
        self.store['youtube-token'] = value
        return self.status()

    def disconnect(self):
        # Removing local authorization stops all future uploads from this service.
        self.store.pop('youtube-token', None)
        return self.status()

    def upload(self, job_id, video, design, privacy, publish_at=None):
        from google.oauth2.credentials import Credentials
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
        from googleapiclient.http import MediaFileUpload
        if privacy not in ('private', 'unlisted', 'public'):
            raise ValueError('Invalid YouTube visibility')
        token = self.store.get('youtube-token')
        if not token:
            raise RuntimeError('Connect YouTube before enabling automatic publishing')
        credentials = Credentials.from_authorized_user_info(token, [SCOPE])
        if credentials.expired and credentials.refresh_token:
            credentials.refresh(Request())
            self.store['youtube-token'] = json.loads(credentials.to_json())
        if not credentials.valid:
            raise RuntimeError('YouTube authorization expired. Reconnect the channel.')
        youtube = build('youtube', 'v3', credentials=credentials, cache_discovery=False)
        key = 'upload:' + job_id
        receipt = {'state': 'upload_started', 'startedAt': time.time(), 'privacy': privacy}
        if not self.store.put(key, receipt, skip_if_exists=True):
            previous = self.store.get(key)
            if previous.get('state') == 'posted':
                return previous
            raise RuntimeError('A previous upload outcome needs checking in YouTube Studio. It will not be retried automatically.')
        credits = '\n'.join(x.get('title', '') + '\n' + x.get('url', '') + '\n' + x.get('credit', '')
                            for x in design.get('sources', []))
        description = design.get('description', '').strip()
        if credits:
            description += '\n\nSources and media credits:\n' + credits
        # Keep all attribution. Never publish with silently truncated media credits.
        if len(description) > 4800:
            receipt.update(state='failed', message='Sources exceed the YouTube description limit; shorten credits before publishing.')
            self.store[key] = receipt
            raise RuntimeError(receipt['message'])
        try:
            request = youtube.videos().insert(part='snippet,status', body={
                'snippet': {'title': design['title'][:100], 'description': description + '\n#Shorts',
                            'categoryId': '27'},
                # A scheduled film is uploaded private and YouTube makes it public at publishAt.
                'status': {'privacyStatus': 'private' if publish_at else privacy, 'selfDeclaredMadeForKids': False,
                           **({'publishAt': publish_at} if publish_at else {})}},
                media_body=MediaFileUpload(str(video), mimetype='video/mp4', resumable=True))
            result = None
            while result is None:
                _, result = request.next_chunk(num_retries=0)
            receipt.update(state='posted', url='https://www.youtube.com/shorts/' + result['id'],
                           videoId=result['id'], finishedAt=time.time(), scheduledFor=publish_at)
            self.store[key] = receipt
            return receipt
        except Exception:
            receipt.update(state='unknown', message='Check YouTube Studio before any new upload attempt.')
            self.store[key] = receipt
            raise RuntimeError('Upload outcome is unknown. Check YouTube Studio; this job will not upload again automatically.') from None
