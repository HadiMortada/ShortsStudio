"""OAuth stays outside Claude's inputs; uploads use an at-most-once receipt."""
import hashlib
import json
import secrets
import time
from pathlib import Path
from urllib.parse import urlsplit

SCOPE = 'https://www.googleapis.com/auth/youtube.upload'


CATEGORIES = {'film & animation': '1', 'people & blogs': '22', 'entertainment': '24', 'howto & style': '26',
              'education': '27', 'science & technology': '28'}


def _clean(value, limit):
    value = ' '.join(str(value or '').replace('<', '').replace('>', '').split())
    return value[:limit].rstrip()


STOPWORDS = set("""the a an and or but of to in on at for with from by is are was were be been this that these those
it its your you our we they how why what when where who which do does did can will not no yes into about over than then
just very more most also only all any each every some such out up off so if as there here new top facts fact""".split()) | {
    'من', 'في', 'على', 'إلى', 'عن', 'مع', 'هذا', 'هذه', 'ذلك', 'التي', 'الذي', 'كيف', 'لماذا', 'ماذا', 'هل', 'ما', 'كل', 'أن', 'إن', 'لا', 'نعم'}
GENERIC = {'ar': ['#معلومات', '#هل_تعلم', '#حقائق'], 'en': ['#facts', '#didyouknow', '#learn']}


def hashtag(value):
    word = ''.join(ch for ch in str(value).strip().lstrip('#').replace(' ', '_') if ch.isalnum() or ch == '_').strip('_')[:40]
    return '#' + word if word else ''


def auto_hashtags(design, language):
    """Topic hashtags from the title when the writer gave too few, then common ones for the language."""
    text = ' '.join([str(design.get('title') or ''), str((design.get('youtube') or {}).get('title') or '')])
    words = [''.join(ch for ch in w if ch.isalnum()) for w in text.split()]
    picked = [w for w in words if len(w) >= 4 and w.lower() not in STOPWORDS and not w.isdigit()]
    return [hashtag(w.lower() if w.isascii() else w) for w in picked[:3]] + GENERIC.get(language, GENERIC['en'])


MAX_HASHTAGS = 10  # YouTube ignores every hashtag on a video that has more than 15


def youtube_pack(design, extra=(), exact=False):
    """Title, description, hashtags, tags, category and language for a Short, from the writer's
    YouTube package with safe fallbacks. Always 3-5 hashtags with #Shorts first: the channel's own
    hashtags, then the writer's, then automatic ones; the first topic hashtags also end the title.
    With exact=True only `extra` is used (the owner edited this film's hashtags)."""
    pack = design.get('youtube') if isinstance(design.get('youtube'), dict) else {}
    language = str(design.get('language') or 'en').split('-')[0].lower()[:3]
    seen, hashtags = {'#shorts'}, ['#Shorts']
    candidates = list(extra) if exact else [*extra, *(pack.get('hashtags') or [])[:6], *auto_hashtags(design, language)]
    for candidate in candidates:
        tag = hashtag(candidate)
        if tag and tag.lower() not in seen and len(hashtags) < MAX_HASHTAGS:
            seen.add(tag.lower())
            hashtags.append(tag)
    title = _clean(pack.get('title') or design.get('title') or 'Shorts Studio film', 100)
    for tag in hashtags[1:3]:
        if tag.lower() not in title.lower() and len(title) + len(tag) + 1 <= 100:
            title += ' ' + tag
    tags, length = [], 0
    for tag in (pack.get('tags') or [])[:15]:
        tag = _clean(tag, 60).replace(',', ' ')
        if tag and length + len(tag) + 2 <= 450:
            tags.append(tag)
            length += len(tag) + 2
    return {'title': title, 'description': _clean(pack.get('description') or design.get('description') or '', 1500),
            'hashtags': hashtags, 'tags': tags, 'language': language,
            'categoryId': CATEGORIES.get(str(pack.get('category', '')).strip().lower(), '27')}


def channel_hashtags(settings_path='shorts-studio/autopilot/settings.json'):
    """The hashtags the owner wants on every post (Settings → Hashtags on every post)."""
    try:
        import json as _json
        value = _json.loads(Path(settings_path).read_text(encoding='utf-8')).get('hashtags') or []
        return [str(x) for x in value][:8] if isinstance(value, list) else []
    except (OSError, ValueError, AttributeError):
        return []


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

    def upload(self, job_id, video, design, privacy, publish_at=None, hashtags=(), exact=False):
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
        pack = youtube_pack(design, hashtags, exact)
        description = (pack['description'] + '\n\n' + ' '.join(pack['hashtags'])).strip()
        if credits:
            description += '\n\nSources and media credits:\n' + credits
        # Keep all attribution. Never publish with silently truncated media credits.
        if len(description) > 4800:
            receipt.update(state='failed', message='Sources exceed the YouTube description limit; shorten credits before publishing.')
            self.store[key] = receipt
            raise RuntimeError(receipt['message'])
        try:
            request = youtube.videos().insert(part='snippet,status', body={
                'snippet': {'title': pack['title'], 'description': description, 'tags': pack['tags'],
                            'categoryId': pack['categoryId'], 'defaultLanguage': pack['language'],
                            'defaultAudioLanguage': pack['language']},
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
