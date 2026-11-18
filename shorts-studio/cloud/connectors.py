"""Optional paid connectors that make films better (Settings → Connectors on the page):

- Higgsfield: AI images (and short AI video clips made from them) for scenes that cannot be
  photographed: history, stories, imagined or inside views. Two ways in: "Sign in with Higgsfield"
  (the owner's plan credits, through Higgsfield's MCP; the page keeps the sign-in and hands the job
  a short-lived token) or a developer API key, HIGGSFIELD_API_KEY ("id:secret", billed in dollars).
- ElevenLabs: premium narration with exact word timings. Key: ELEVENLABS_API_KEY.

Each is used only when its key is saved, it is switched on, and the owner's limits allow it: a
number of AI images and clips per film, and a monthly budget per service. Spending is estimated
from list prices and reported to the page as soon as it happens (the providers' own dashboards
show the exact bill). Anything that fails falls back to the free path: drawn illustration, real
archive photos and the free voices.
"""
import base64
import json
import re
import sys
import time
import uuid
from datetime import datetime, timezone
from urllib.request import Request, urlopen

# Estimates (US$) from the providers' price lists in November 2026; kept on the safe side.
IMAGE_COST = 0.06              # one 2K Higgsfield image
CLIP_SECONDS = 5
CLIP_COST = 0.45               # one 5-second image-to-video clip
VOICE_COST_PER_CHAR = 0.00012  # ElevenLabs multilingual, about $0.12 per 1,000 characters

HIGGSFIELD = 'https://api.higgsfield.ai'
IMAGE_MODEL = '/higgsfield-ai/soul/standard'
CLIP_MODEL = '/kling-video/v2.5-turbo/standard/image-to-video'
ELEVEN = 'https://api.elevenlabs.io'
ELEVEN_MODEL = 'eleven_multilingual_v2'
DEFAULT_VOICE = 'JBFqnCBsd6RMkjVDRZzb'  # ElevenLabs' premade "George"; speaks every supported language

# Plan credits through Higgsfield's MCP (quotes from the MCP's estimate tools, November 2026).
MCP_URL = 'https://mcp.higgsfield.ai/mcp'
PLAN_IMAGE_MODEL, PLAN_IMAGE_CREDITS = 'gpt_image_2_6', 0.25
PLAN_CLIP_MODEL, PLAN_CLIP_CREDITS = 'kling3_0', 6.25   # 5 s, standard, silent

DEFAULTS = {'ai': {'images': True, 'clips': False, 'maxImages': 4, 'maxClips': 1, 'budget': 10.0, 'credits': 100.0,
                   'via': 'plan'},
            'voice': {'enabled': True, 'en': '', 'ar': '', 'budget': 5.0}}
MAX_DOWNLOAD = 60 * 1024 * 1024


class ConnectorError(RuntimeError):
    pass


def _number(value, low, high, default):
    try:
        value = float(value)
    except (TypeError, ValueError):
        return default
    return max(low, min(high, value))


def settings(studio):
    """The owner's connector settings with safe defaults."""
    raw = studio.get('connectors') if isinstance(studio, dict) and isinstance(studio.get('connectors'), dict) else {}
    ai, voice = raw.get('ai') or {}, raw.get('voice') or {}
    voice_id = lambda v: v if isinstance(v, str) and re.fullmatch(r'[A-Za-z0-9]{10,40}', v) else ''
    return {'ai': {'images': bool(ai.get('images', DEFAULTS['ai']['images'])),
                   'clips': bool(ai.get('clips', DEFAULTS['ai']['clips'])),
                   'maxImages': int(_number(ai.get('maxImages'), 0, 8, DEFAULTS['ai']['maxImages'])),
                   'maxClips': int(_number(ai.get('maxClips'), 0, 3, DEFAULTS['ai']['maxClips'])),
                   'budget': _number(ai.get('budget'), 0, 1000, DEFAULTS['ai']['budget']),
                   'credits': _number(ai.get('credits'), 0, 100000, DEFAULTS['ai']['credits']),
                   'via': ai.get('via') if ai.get('via') in ('plan', 'key') else DEFAULTS['ai']['via']},
            'voice': {'enabled': bool(voice.get('enabled', DEFAULTS['voice']['enabled'])),
                      'en': voice_id(voice.get('en')), 'ar': voice_id(voice.get('ar')),
                      'budget': _number(voice.get('budget'), 0, 1000, DEFAULTS['voice']['budget'])}}


def limits(config):
    """Monthly limits per service: dollars for the API and voices, credits for the Higgsfield plan."""
    return {'higgsfield': config['ai']['budget'], 'higgsfield_credits': config['ai']['credits'],
            'elevenlabs': config['voice']['budget']}


def month():
    return datetime.now(timezone.utc).strftime('%Y-%m')


class Budget:
    """This month's spending per service, read from the page and reported back as it grows."""

    def __init__(self, limits, spent=None, report=None):
        self.limits, self.report = dict(limits), report
        spent = spent if isinstance(spent, dict) and spent.get('month') == month() else {}
        self.spent = {k: float(spent.get(k) or 0) for k in limits}
        self.film = {k: 0.0 for k in limits}

    def left(self, service):
        return max(0.0, self.limits.get(service, 0) - self.spent.get(service, 0))

    def allows(self, service, amount):
        return amount <= self.left(service) + 1e-9

    def charge(self, service, amount):
        self.spent[service] = self.spent.get(service, 0) + amount
        self.film[service] = self.film.get(service, 0) + amount
        if self.report:
            try:
                self.report(service, amount)
            except Exception as error:
                print('Spending could not be reported:', str(error)[:200], file=sys.stderr)


class Higgsfield:
    """The developer API, billed in dollars."""
    service, image_cost, clip_cost = 'higgsfield', IMAGE_COST, CLIP_COST

    def __init__(self, key, opener=urlopen, sleep=time.sleep, clock=time.monotonic):
        if not re.fullmatch(r'[^:\s]{4,200}:[^:\s]{4,400}', key or ''):
            raise ConnectorError('The Higgsfield key must look like "key-id:key-secret".')
        self.key, self.opener, self.sleep, self.clock = key, opener, sleep, clock

    def _call(self, method, path, body=None, extra=None):
        headers = {'Authorization': 'Key ' + self.key, 'Content-Type': 'application/json', 'User-Agent': 'ShortsStudio',
                   **(extra or {})}
        request = Request(HIGGSFIELD + path, data=json.dumps(body).encode() if body is not None else None,
                          method=method, headers=headers)
        with self.opener(request, timeout=60) as response:
            return json.loads(response.read().decode() or '{}')

    def run(self, path, body, timeout):
        submitted = self._call('POST', path, body, {'Idempotency-Key': str(uuid.uuid4())})
        request_id = str(submitted.get('request_id', ''))
        if not re.fullmatch(r'[0-9a-f-]{36}', request_id):
            raise ConnectorError('Higgsfield did not accept the request.')
        started, wait = self.clock(), 3
        while True:
            status = self._call('GET', f'/requests/{request_id}/status')
            state = status.get('status')
            if state == 'completed':
                return status
            if state in ('failed', 'nsfw', 'canceled'):
                raise ConnectorError(f'Higgsfield {state}: {str(status.get("error") or "")[:160]}')
            if self.clock() - started > timeout:
                try:
                    self._call('POST', f'/requests/{request_id}/cancel')
                except Exception:
                    pass
                raise ConnectorError('Higgsfield took too long.')
            self.sleep(wait)
            wait = min(wait * 1.5, 15)

    def image(self, prompt):
        done = self.run(IMAGE_MODEL, {'prompt': prompt[:1500], 'aspect_ratio': '9:16', 'resolution': '2K',
                                      'num_images': 1}, timeout=240)
        url = ((done.get('images') or [{}])[0]).get('url', '')
        return {'url': url, 'ref': url}

    def clip(self, image, prompt):
        done = self.run(CLIP_MODEL, {'prompt': prompt[:1500], 'image_url': image['url'], 'duration': CLIP_SECONDS},
                        timeout=600)
        return (done.get('video') or {}).get('url', '')

    def download(self, url):
        if not str(url).startswith('https://'):
            raise ConnectorError('Higgsfield returned no file.')
        with self.opener(Request(url, headers={'User-Agent': 'ShortsStudio'}), timeout=120) as response:
            data = response.read(MAX_DOWNLOAD + 1)
        if len(data) > MAX_DOWNLOAD:
            raise ConnectorError('The generated file is too large.')
        return data


UUID = re.compile(r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}')
DONE = {'completed', 'complete', 'success', 'succeeded', 'done', 'finished'}
FAILED = {'failed', 'error', 'nsfw', 'ip_detected', 'canceled', 'cancelled', 'rejected'}


class McpClient:
    """A small MCP client (streamable HTTP, JSON-RPC) for one Higgsfield session."""

    def __init__(self, token, opener=urlopen, url=MCP_URL):
        self.token, self.opener, self.url, self.session, self.counter = token, opener, url, '', 0

    def _post(self, payload, expect=True):
        headers = {'Authorization': 'Bearer ' + self.token, 'Content-Type': 'application/json',
                   'Accept': 'application/json, text/event-stream', 'User-Agent': 'ShortsStudio'}
        if self.session:
            headers['Mcp-Session-Id'] = self.session
        request = Request(self.url, data=json.dumps(payload).encode(), method='POST', headers=headers)
        with self.opener(request, timeout=90) as response:
            self.session = response.headers.get('Mcp-Session-Id') or self.session
            body = response.read().decode(errors='replace')
        if not expect:
            return None
        if body.lstrip().startswith('{'):
            return json.loads(body)
        for line in reversed(body.splitlines()):  # a server-sent event stream: the last data line answers
            if line.startswith('data:') and line[5:].strip().startswith('{'):
                message = json.loads(line[5:])
                if message.get('id') == payload.get('id'):
                    return message
        raise ConnectorError('Higgsfield sent an answer that could not be read.')

    def start(self):
        self.counter += 1
        self._post({'jsonrpc': '2.0', 'id': self.counter, 'method': 'initialize', 'params': {
            'protocolVersion': '2025-06-18', 'capabilities': {}, 'clientInfo': {'name': 'ShortsStudio', 'version': '1'}}})
        self._post({'jsonrpc': '2.0', 'method': 'notifications/initialized'}, expect=False)

    def call(self, tool, arguments):
        if not self.counter:
            self.start()
        self.counter += 1
        answer = self._post({'jsonrpc': '2.0', 'id': self.counter, 'method': 'tools/call',
                             'params': {'name': tool, 'arguments': arguments}})
        if answer.get('error'):
            raise ConnectorError('Higgsfield: ' + str(answer['error'].get('message', 'request refused'))[:200])
        result = answer.get('result') or {}
        text = ' '.join(c.get('text', '') for c in result.get('content') or [] if c.get('type') == 'text')
        if result.get('isError'):
            raise ConnectorError('Higgsfield: ' + text[:200])
        if isinstance(result.get('structuredContent'), dict):
            return result['structuredContent']
        try:
            return json.loads(text)
        except ValueError:
            return {'text': text}


def _find(value, test):
    """Every nested value that passes test (dicts and lists are searched)."""
    found = []
    if test(value):
        found.append(value)
    if isinstance(value, dict):
        for item in value.values():
            found += _find(item, test)
    elif isinstance(value, list):
        for item in value:
            found += _find(item, test)
    return found


class HiggsfieldPlan:
    """The owner's Higgsfield plan, through Higgsfield's MCP; costs are counted in credits."""
    service, image_cost, clip_cost = 'higgsfield_credits', PLAN_IMAGE_CREDITS, PLAN_CLIP_CREDITS

    def __init__(self, token, opener=urlopen, sleep=time.sleep, clock=time.monotonic):
        self.mcp, self.opener, self.sleep, self.clock = McpClient(token, opener), opener, sleep, clock

    def _submit(self, tool, params):
        answer = self.mcp.call(tool, {'requests': [{'index': 0, 'params': {**params, 'use_unlim': False}}],
                                      'context': 'Illustrating one scene of a short educational video.'})
        if _find(answer, lambda v: isinstance(v, dict) and 'unlim_choice' in v):
            raise ConnectorError('Higgsfield asked which balance to use; nothing was generated.')
        ids = [v for v in _find(answer, lambda v: isinstance(v, dict) and isinstance(v.get('job_id'), str))
               if UUID.fullmatch(v['job_id'])]
        if not ids:
            raise ConnectorError('Higgsfield did not start the generation: ' + json.dumps(answer)[:200])
        return ids[0]['job_id']

    def _wait(self, job_id, timeout):
        started = self.clock()
        while True:
            answer = self.mcp.call('jobs_wait', {'jobs': [{'index': 0, 'job_id': job_id}], 'timeout_seconds': 15})
            jobs = [v for v in _find(answer, lambda v: isinstance(v, dict) and v.get('job_id') == job_id)] or [answer]
            job = jobs[0]
            state = str(job.get('status') or '').lower()
            if state in DONE:
                urls = _find(job, lambda v: isinstance(v, str) and v.startswith('https://'))
                if not urls:
                    raise ConnectorError('Higgsfield finished without a file.')
                return urls[0]
            if state in FAILED:
                raise ConnectorError(f'Higgsfield {state}: {str(job.get("error") or "")[:160]}')
            if self.clock() - started > timeout:
                raise ConnectorError('Higgsfield took too long.')
            pause = answer.get('poll_after_seconds') if isinstance(answer, dict) else None
            self.sleep(min(max(float(pause or 2), 1), 20))

    def image(self, prompt):
        job = self._submit('generate_image_batch', {'model': PLAN_IMAGE_MODEL, 'prompt': prompt[:1500], 'aspect_ratio': '9:16'})
        return {'url': self._wait(job, 300), 'ref': job}

    def clip(self, image, prompt):
        job = self._submit('generate_video_batch', {'model': PLAN_CLIP_MODEL, 'prompt': prompt[:1500], 'aspect_ratio': '9:16',
                                                    'duration': CLIP_SECONDS, 'mode': 'std', 'sound': 'off',
                                                    'medias': [{'value': image['ref'], 'role': 'start_image'}]})
        return self._wait(job, 900)

    def download(self, url):
        return Higgsfield.download(self, url)


def eleven_words(alignment):
    """Word timings (seconds) from ElevenLabs' per-character alignment."""
    chars = alignment.get('characters') or []
    starts = alignment.get('character_start_times_seconds') or []
    ends = alignment.get('character_end_times_seconds') or []
    words, current = [], None
    for char, start, end in zip(chars, starts, ends):
        if char.isspace():
            if current:
                words.append(current)
            current = None
        elif current is None:
            current = {'start': round(float(start), 3), 'end': round(float(end), 3), 'text': char}
        else:
            current['text'] += char
            current['end'] = round(float(end), 3)
    if current:
        words.append(current)
    return words


class Connectors:
    """What this film may use, decided once before Claude writes it."""

    def __init__(self, studio, source, budget=None, opener=urlopen, sleep=time.sleep, plan_token=''):
        self.config = settings(studio)
        self.budget = budget or Budget(limits(self.config))
        self.opener, self.used, self.granted = opener, {'images': 0, 'clips': 0}, None
        self.higgsfield = None
        key = (source.get('HIGGSFIELD_API_KEY') or '').strip()
        if self.config['ai']['images'] or self.config['ai']['clips']:
            # The owner's plan (signed in) or the developer key; "via" says which comes first.
            choices = [('plan', plan_token), ('key', key)]
            if self.config['ai']['via'] == 'key':
                choices.reverse()
            for kind, secret in choices:
                if not secret:
                    continue
                try:
                    self.higgsfield = HiggsfieldPlan(secret, opener, sleep) if kind == 'plan' else Higgsfield(secret, opener, sleep)
                    break
                except ConnectorError as error:
                    print(error, file=sys.stderr)
        self.eleven_key = (source.get('ELEVENLABS_API_KEY') or '').strip() if self.config['voice']['enabled'] else ''

    # ---- AI images and clips ----
    def allowance(self):
        if not self.higgsfield:
            return {'images': 0, 'clips': 0}
        gen = self.higgsfield
        ai, left = self.config['ai'], self.budget.left(gen.service)
        clip = gen.image_cost + gen.clip_cost
        clips = min(ai['maxClips'] if ai['clips'] else 0, int(left // clip + 1e-9))
        images = min(ai['maxImages'] if ai['images'] else 0, int((left - clips * clip) // gen.image_cost + 1e-9))
        return {'images': max(0, images), 'clips': max(0, clips)}

    def prompt(self):
        # What Claude is told it may use is also the most this film can spend.
        allowed = self.granted = self.allowance()
        if not allowed['images'] and not allowed['clips']:
            return ''
        return ('\nAI GENERATION (paid, so use it where it adds the most): this film may also request up to '
                f'{allowed["images"]} AI images and {allowed["clips"]} AI video clips as assets with source "ai". '
                'Use them for scenes that cannot be photographed (historical moments, stories, imagined or inside '
                'views, things too small or too far away), never for something a real archive photo can show. '
                'query: a vivid shot description in English (subject, setting, light, camera angle, style), vertical '
                f'9:16. kind "image" for a still you animate yourself, kind "video" for a {CLIP_SECONDS}-second moving '
                'shot. Never show the face of any prophet, companion, saint or other real historical or religious '
                'person: show places, objects, hands or figures from behind instead. Never imitate a real photo of a '
                'real event. Put a small "AI illustration" label on these shots while they are on screen.\n')

    def media(self, request, destination):
        """An AI image or clip for one asset request; returns its credit like the archive sources."""
        from resources import normalize_media
        if not self.higgsfield:
            raise ConnectorError('AI generation is not set up.')
        kind, granted = request.get('kind', 'image'), self.granted or self.allowance()
        if kind == 'video' and self.used['clips'] >= granted['clips']:
            raise ConnectorError('No AI clips left for this film.')
        if kind == 'image' and self.used['images'] >= granted['images']:
            raise ConnectorError('No AI images left for this film.')
        prompt = str(request.get('query') or request.get('title') or '').strip()
        if len(prompt) < 3:
            raise ConnectorError('The AI shot needs a description.')
        gen = self.higgsfield
        cost = gen.image_cost + (gen.clip_cost if kind == 'video' else 0)
        if not self.budget.allows(gen.service, cost):
            raise ConnectorError('The monthly AI budget is used up.')
        image = gen.image(prompt)
        self.budget.charge(gen.service, gen.image_cost)
        if kind == 'video':
            clip_url = gen.clip(image, prompt)
            self.budget.charge(gen.service, gen.clip_cost)
            normalize_media(gen.download(clip_url), destination, 'video')
            self.used['clips'] += 1
        else:
            normalize_media(gen.download(image['url']), destination, 'image')
            self.used['images'] += 1
        return {'title': 'AI illustration: ' + prompt[:120], 'url': '', 'credit': 'AI-generated with Higgsfield',
                'license': 'AI-generated', 'asset': 'assets/' + destination.name, 'source': 'AI'}

    # ---- Premium voice ----
    def voice(self):
        """An async narrator (text, voice, output) for ElevenLabs, or None when it is not set up."""
        if not self.eleven_key or self.budget.left('elevenlabs') <= 0:
            return None
        config, key, opener, budget = self.config['voice'], self.eleven_key, self.opener, self.budget

        async def eleven(text, voice, output):
            import asyncio
            from production import VoiceUnavailable
            language = str(voice or 'en').split('-')[0].lower()
            voice_id = config.get(language) or config.get('en') or DEFAULT_VOICE
            cost = len(text) * VOICE_COST_PER_CHAR
            if not budget.allows('elevenlabs', cost):
                raise VoiceUnavailable('The monthly premium voice budget is used up.')
            body = json.dumps({'text': text, 'model_id': ELEVEN_MODEL,
                               'voice_settings': {'stability': 0.45, 'similarity_boost': 0.8, 'style': 0.25}}).encode()
            request = Request(f'{ELEVEN}/v1/text-to-speech/{voice_id}/with-timestamps?output_format=mp3_44100_128',
                              data=body, method='POST', headers={'xi-api-key': key, 'Content-Type': 'application/json',
                                                                 'User-Agent': 'ShortsStudio'})

            def call():
                with opener(request, timeout=90) as response:
                    return json.loads(response.read().decode())
            try:
                answer = await asyncio.to_thread(call)
                audio = base64.b64decode(answer.get('audio_base64') or '')
            except Exception as error:
                print('Premium voice unavailable:', type(error).__name__, str(error)[:200], file=sys.stderr, flush=True)
                raise VoiceUnavailable('The premium voice service did not answer.') from None
            if len(audio) < 1000:
                raise VoiceUnavailable('The premium voice returned no audio.')
            budget.charge('elevenlabs', cost)
            output.write_bytes(audio)
            output.with_suffix('.words.json').write_text(
                json.dumps(eleven_words(answer.get('alignment') or {}), ensure_ascii=False))
        return eleven

    def summary(self):
        return {k: round(v, 4) for k, v in self.budget.film.items() if v}


# ---- Spending, kept by the page (STUDIO_SPEND) ----
def page_spent(source=None, opener=urlopen):
    from notify import page_settings
    page, key = page_settings(source)
    if not page:
        return None
    try:
        with opener(Request(page + '/api/spend', headers={'Authorization': 'Bearer ' + key, 'User-Agent': 'ShortsStudio'}),
                    timeout=20) as response:
            return json.loads(response.read().decode())
    except Exception:
        return None


def page_reporter(source=None, opener=urlopen):
    from notify import page_settings
    page, key = page_settings(source)
    if not page:
        return None

    def report(service, amount):
        body = json.dumps({'service': service, 'amount': round(amount, 4), 'month': month()}).encode()
        with opener(Request(page + '/api/spend/report', data=body, method='POST',
                            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                                     'User-Agent': 'ShortsStudio'}), timeout=20):
            pass
    return report


def page_plan_token(source=None, opener=urlopen):
    """A short-lived Higgsfield token for the owner's plan ('' when not signed in)."""
    from notify import page_settings
    page, key = page_settings(source)
    if not page:
        return ''
    try:
        with opener(Request(page + '/api/higgsfield/token', data=b'{}', method='POST',
                            headers={'Authorization': 'Bearer ' + key, 'Content-Type': 'application/json',
                                     'User-Agent': 'ShortsStudio'}), timeout=30) as response:
            token = json.loads(response.read().decode()).get('access_token', '')
            return token if isinstance(token, str) and 20 <= len(token) <= 8000 else ''
    except Exception:
        return ''


def from_environment(studio, source=None):
    """The film job's connectors: keys from Actions secrets, the plan sign-in and spending from the page."""
    import os
    source = os.environ if source is None else source
    config = settings(studio)
    wants_ai = config['ai']['images'] or config['ai']['clips']
    plan = page_plan_token(source) if wants_ai else ''
    if not (plan or (source.get('HIGGSFIELD_API_KEY') or '').strip() or (source.get('ELEVENLABS_API_KEY') or '').strip()):
        return None
    budget = Budget(limits(config), page_spent(source), page_reporter(source))
    return Connectors(studio, source, budget, plan_token=plan)
