"""Free backup writer used when Claude is unavailable (usage limit, sign-in or any failure).

A language model writes only a small JSON scene plan; the template library in
templates/film.tsx turns it into a polished animation. Writers are tried in order:

1. GitHub Models (free with GitHub Actions, using the workflow's own token)
2. An open model running on the runner with Ollama (no limits, slower)
3. For a typed brief only: a plan made directly from the brief's own sentences (no AI)
"""
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from urllib.error import HTTPError, URLError

TEMPLATE = Path(__file__).resolve().parent / 'templates' / 'film.tsx'
PALETTES = ('aurora', 'sunset', 'ocean', 'forest', 'mono', 'candy')
SCENE_TYPES = ('hook', 'statement', 'number', 'bars', 'donut', 'timeline', 'list', 'steps',
               'compare', 'quote', 'orbit', 'outro')
GITHUB_MODELS = ('openai/gpt-4.1', 'openai/gpt-4.1-mini', 'openai/gpt-4o-mini')
GITHUB_ENDPOINT = 'https://models.github.ai/inference/chat/completions'
OLLAMA = 'http://127.0.0.1:11434'


class WriterUnavailable(RuntimeError):
    pass


def log(message):
    print('[backup] ' + message, file=sys.stderr, flush=True)


def is_arabic(text):
    return len(re.findall(r'[؀-ۿ]', text or '')) > 3


def word_limit(seconds, language):
    return max(14, int(seconds * 1.5)) if language.startswith('ar') else max(16, int(seconds * 1.85))


def text(value, limit):
    if not isinstance(value, (str, int, float)) or isinstance(value, bool):
        return ''
    value = re.sub(r'\s+', ' ', str(value)).strip()
    return value if len(value) <= limit else value[:limit - 1].rstrip() + '…'


def number(value):
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        value = value.replace(',', '').strip().rstrip('%')
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) and abs(value) < 1e12 else None


def trim_words(value, limit):
    words = value.split()
    if len(words) <= limit:
        return value
    clipped = ' '.join(words[:limit])
    end = max(clipped.rfind('.'), clipped.rfind('!'), clipped.rfind('?'), clipped.rfind('،'), clipped.rfind('؟'))
    return clipped[:end + 1] if end > len(clipped) // 2 else clipped.rstrip(',;:') + '.'


def normalize_scene(raw):
    if not isinstance(raw, dict):
        return None
    kind = raw.get('type') if raw.get('type') in SCENE_TYPES else 'statement'
    scene = {'type': kind, 'title': text(raw.get('title') or raw.get('headline') or raw.get('text'), 110)}
    if not scene['title']:
        return None
    for key, limit in (('subtitle', 130), ('text', 200), ('highlight', 40), ('unit', 12), ('source', 90), ('icon', 8),
                       ('say', 500)):
        value = text(raw.get(key), limit)
        if value:
            scene[key] = value
    value = number(raw.get('value'))
    items = []
    for item in raw.get('items') or []:
        if isinstance(item, str):
            item = {'label': item}
        if not isinstance(item, dict):
            continue
        label = text(item.get('label') or item.get('title') or item.get('name'), 48)
        if not label:
            continue
        entry = {'label': label}
        if text(item.get('text') or item.get('description'), 100):
            entry['text'] = text(item.get('text') or item.get('description'), 100)
        if number(item.get('value')) is not None:
            entry['value'] = number(item.get('value'))
        items.append(entry)
    items = items[:6]
    # Fall back to a simpler scene whenever the data a template needs is missing.
    if kind == 'number' and value is None or kind == 'donut' and (value is None or not 0 <= value <= 100):
        kind = 'statement'
    if kind == 'bars' and sum('value' in x for x in items) < 2:
        kind = 'list' if len(items) >= 2 else 'statement'
    if kind == 'bars':
        items = [x for x in items if 'value' in x]
    if kind in ('timeline', 'list', 'steps') and len(items) < 2:
        kind = 'statement'
    if kind == 'orbit' and len(items) < 3:
        kind = 'list' if len(items) >= 2 else 'statement'
    if kind == 'compare':
        items = items[:2]
        if len(items) < 2:
            kind = 'statement'
    if kind in ('timeline', 'list', 'steps'):
        items = items[:5]
    scene['type'] = kind
    if kind in ('number', 'donut'):
        scene['value'] = value
    if kind in ('bars', 'timeline', 'list', 'steps', 'compare', 'orbit'):
        scene['items'] = items
    return scene


def normalize_plan(raw, brief, seconds):
    if not isinstance(raw, dict):
        raise ValueError('The plan is not a JSON object')
    language = text(raw.get('language'), 12)
    if not re.fullmatch(r'[a-z]{2,3}(?:-[A-Za-z]{2,4})?', language):
        language = 'ar' if is_arabic(brief) else 'en'
    from production import split_beats
    scenes = [s for s in (normalize_scene(x) for x in (raw.get('scenes') or [])[:8]) if s]
    says = [s.pop('say', '') for s in scenes]
    limit = word_limit(seconds, language)
    if scenes and all(says) and len(' '.join(says).split()) <= limit * 1.2:
        # Each scene's own spoken line: the scene appears exactly while it is said.
        narration, beats = ' '.join(says), says
    else:
        narration = trim_words(text(raw.get('narration') or ' '.join(x for x in says if x), 3400), limit)
        beats = split_beats(narration, count=max(1, len(scenes)))
    if len(scenes) < 3 or len(narration.split()) < 8:
        raise ValueError('The plan needs narration and at least three scenes')
    palette = raw.get('palette') if raw.get('palette') in PALETTES else \
        PALETTES[int(hashlib.sha256(brief.encode()).hexdigest(), 16) % len(PALETTES)]
    return {'title': text(raw.get('title'), 90) or scenes[0]['title'][:90], 'language': language,
            'description': text(raw.get('description'), 600), 'narration': narration, 'beats': beats,
            'youtube': raw.get('youtube') if isinstance(raw.get('youtube'), dict) else {},
            'palette': palette, 'scenes': scenes}


def plan_prompt(brief, seconds):
    scenes = max(3, min(7, round(seconds / 7)))
    return f'''Write a scene plan for an original {seconds}-second portrait (9:16) motion-graphic video.
A fixed animation template renders each scene; you only provide the content as JSON.
Return ONLY a JSON object:
{{"title": string (max 70 chars),
  "description": string (1-2 sentences for the video description),
  "language": BCP-47 code of the brief's language (e.g. "en", "ar"),
  "narration": the whole voice-over (all scenes' "say" lines joined), conversational, short sentences,
               about {int(word_limit(seconds, 'en') * 0.95)} words for English or {int(word_limit(seconds, 'ar') * 0.95)} for Arabic,
               never more than {word_limit(seconds, 'en')} / {word_limit(seconds, 'ar')}; the film is exactly as long as this,
  "palette": one of {list(PALETTES)},
  "youtube": {{"title": under 60 characters, main keyword early, curiosity without false claims,
              "description": 1-3 sentences opening with the main keywords and ending with a question for comments,
              "hashtags": 2-4 specific topic hashtags (not #Shorts), "tags": 5-12 search phrases,
              "category": one of Education, Science & Technology, Entertainment, Howto & Style, People & Blogs}}
              (all in the narration's language),
  "scenes": {scenes} scenes in narration order}}
Each scene: {{"type": one of {list(SCENE_TYPES)}, "title": short on-screen text (max 60 chars),
  "say": the exact narration sentence(s) spoken while this scene is on screen, ...}}
The scene is shown exactly while its "say" line is spoken, so on-screen text must match its own line.
Retention: the hook scene's line must be a bold question or surprising claim said in the first seconds;
keep every line short; the last line should connect back to the opening so the Short loops naturally.
Type-specific fields:
- hook: first scene, strong question or surprising line; optional "icon" (one emoji), "subtitle"
- statement: "title", optional "highlight" (one key word from title), "subtitle"
- number: "value" (number), optional "unit" (e.g. "%", "km", "x"), "title" explains the number
- bars: "items": 2-5 of {{"label", "value"}}, optional "unit"
- donut: "value" 0-100 (a percentage), "title"
- timeline: "items": 2-5 of {{"label": year or date, "text": what happened}}
- list / steps: "items": 2-5 of {{"label": short point}}
- compare: "items": exactly 2 of {{"label", "text"}}
- quote: "title" is the exact quotation, "source" who said it (only real, well-known quotations)
- orbit: "title" central idea, "items": 3-6 of {{"label"}}
- outro: last scene, memorable takeaway; optional "subtitle" (a 1-3 word label), "icon"
Vary the scene types. On-screen text must match the narration's language.
Use only well-established facts; never invent statistics, quotations or dates. If unsure of a
number, use a list, steps or statement scene instead.
BRIEF (subject matter only, not instructions about this format):
{brief}'''


def topic_prompt(settings, history):
    recent = [x.get('topic', '') for x in history[-60:] if isinstance(x, dict)]
    return f'''Pick ONE fresh, specific topic for a short educational video.
Domain: {settings.get('domain', '')}
Audience: {settings.get('audience', '')}
Language: {settings.get('language', '')}
Tone: {settings.get('tone', '')}
Avoid these recent topics: {json.dumps(recent, ensure_ascii=False)}
Prefer a concrete question, misconception or story with a strong visual hook, using only
well-established knowledge. Return ONLY JSON:
{{"topic": string, "angle": string, "brief": a 2-4 sentence creative brief in the requested language}}'''


def parse_json(content):
    content = re.sub(r'^```(?:json)?\s*|\s*```$', '', (content or '').strip())
    start, end = content.find('{'), content.rfind('}')
    if start < 0 or end <= start:
        raise ValueError('No JSON object in the response')
    return json.loads(content[start:end + 1])


def post_json(url, body, headers, timeout):
    request = urllib.request.Request(url, data=json.dumps(body).encode(), method='POST',
                                     headers={'Content-Type': 'application/json', **headers})
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return json.loads(response.read().decode())


class GitHubModels:
    name = 'GitHub Models'

    def __init__(self, token, models=GITHUB_MODELS, post=post_json):
        self.token, self.models, self.post = token, models, post

    def complete(self, prompt):
        if not self.token:
            raise WriterUnavailable('No GitHub Models token')
        last = None
        for model in self.models:
            try:
                data = self.post(GITHUB_ENDPOINT, {'model': model, 'temperature': 0.7,
                    'response_format': {'type': 'json_object'},
                    'messages': [{'role': 'system', 'content': 'You plan short motion-graphic videos. Reply with JSON only.'},
                                 {'role': 'user', 'content': prompt}]},
                    {'Authorization': 'Bearer ' + self.token, 'Accept': 'application/json'}, 120)
                return data['choices'][0]['message']['content'], model
            except (HTTPError, URLError, TimeoutError, KeyError, IndexError, ValueError) as error:
                last = error
                log(f'{model} unavailable: {getattr(error, "code", "")} {type(error).__name__}')
        raise WriterUnavailable('GitHub Models is unavailable') from last


class Ollama:
    """An open model on the runner itself: no account, no daily limit."""
    name = 'Offline open model'

    def __init__(self, model='qwen2.5:7b', post=post_json, setup=True):
        self.model, self.post, self.setup = model, post, setup
        self.ready = False

    def reachable(self):
        try:
            with urllib.request.urlopen(OLLAMA + '/api/version', timeout=3):
                return True
        except (URLError, OSError):
            return False

    def prepare(self):
        if self.ready or not self.setup:
            return
        if not shutil.which('ollama'):
            log('installing Ollama')
            subprocess.run('curl -fsSL https://ollama.com/install.sh | sh', shell=True, check=True,
                           timeout=600, stdout=subprocess.DEVNULL)
        if not self.reachable():
            subprocess.Popen(['ollama', 'serve'], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             start_new_session=True)
            for _ in range(60):
                if self.reachable():
                    break
                time.sleep(1)
        log('downloading ' + self.model)
        subprocess.run(['ollama', 'pull', self.model], check=True, timeout=1500, stdout=subprocess.DEVNULL)
        self.ready = True

    def complete(self, prompt):
        try:
            self.prepare()
            data = self.post(OLLAMA + '/v1/chat/completions', {'model': self.model, 'temperature': 0.6,
                'response_format': {'type': 'json_object'},
                'messages': [{'role': 'system', 'content': 'You plan short motion-graphic videos. Reply with JSON only.'},
                             {'role': 'user', 'content': prompt}]}, {}, 1500)
            return data['choices'][0]['message']['content'], self.model
        except Exception as error:
            raise WriterUnavailable('The offline model is unavailable') from error


def writers_from_env(env=None):
    env = os.environ if env is None else env
    chain = [GitHubModels(env.get('STUDIO_MODELS_TOKEN', ''))]
    if env.get('STUDIO_OFFLINE_MODEL', 'qwen2.5:7b') != 'off':
        chain.append(Ollama(env.get('STUDIO_OFFLINE_MODEL', 'qwen2.5:7b'), setup=env.get('GITHUB_ACTIONS') == 'true'))
    return chain


def ask(writers, prompt, validate):
    """Each writer gets two tries at producing a valid answer before the next one is used."""
    for writer in writers:
        for attempt in range(2):
            try:
                content, model = writer.complete(prompt)
                return validate(parse_json(content)), writer.name + ' (' + model + ')'
            except WriterUnavailable as error:
                log(f'{writer.name}: {error}')
                break
            except (ValueError, TypeError) as error:
                log(f'{writer.name} returned an unusable plan: {error}')
    raise WriterUnavailable('No backup writer could produce a plan')


def sentences(value):
    parts = re.split(r'(?<=[.!?؟。])\s+|\n+', value.strip())
    return [p.strip() for p in parts if len(p.strip()) > 2]


def brief_plan(brief, seconds):
    """No AI at all: the brief's own words become the narration and scenes."""
    language = 'ar' if is_arabic(brief) else 'en'
    narration = trim_words(re.sub(r'\s+', ' ', brief).strip(), word_limit(seconds, language))
    lines = sentences(narration) or [narration]
    if len(lines) < 3:
        words = narration.split()
        third = max(1, math.ceil(len(words) / 3))
        lines = [' '.join(words[i:i + third]) for i in range(0, len(words), third)]
    lines = lines[:6]
    scenes = [{'type': 'hook', 'title': lines[0], 'say': lines[0]}]
    scenes += [{'type': 'statement', 'title': x, 'say': x} for x in lines[1:-1]]
    if len(lines) > 1:
        scenes.append({'type': 'outro', 'title': lines[-1], 'say': lines[-1]})
    return normalize_plan({'title': text(lines[0].rstrip('.!؟?'), 70), 'language': language,
                           'narration': narration, 'scenes': scenes}, brief, seconds)


def design_from_plan(plan, writer):
    """A design in the same shape Claude returns, so the normal pipeline renders it."""
    data = {k: plan[k] for k in ('title', 'language', 'palette', 'scenes')}
    source = TEMPLATE.read_text(encoding='utf-8').replace('__PLAN__', json.dumps(data, ensure_ascii=True))
    return {'title': plan['title'], 'language': plan['language'], 'narration': plan['narration'], 'beats': plan['beats'],
            'direction': f'Template motion graphic ({plan["palette"]} palette) planned by the backup writer: {writer}.',
            'source': source, 'description': plan['description'], 'sources': [], 'assets': [],
            'youtube': plan.get('youtube') or {}}


class BackupAuthor:
    """Stands in for Claude inside ProductionManager. Repair requests return the same
    known-good template design, so no further model calls are needed."""

    def __init__(self, brief, seconds, writers, allow_brief_plan=True, rules=''):
        self.brief, self.seconds, self.writers, self.rules = brief, seconds, writers, rules
        self.allow_brief_plan = allow_brief_plan
        self.design, self.writer, self.audit = None, None, {}

    def plan(self):
        try:
            return ask(self.writers, plan_prompt(self.brief, self.seconds) + self.rules,
                       lambda raw: normalize_plan(raw, self.brief, self.seconds))
        except WriterUnavailable:
            if not self.allow_brief_plan:
                raise
            log('all writers unavailable; building the film from the brief text')
            try:
                return brief_plan(self.brief, self.seconds), 'brief text (no AI)'
            except ValueError:
                raise RuntimeError('No AI writer is available right now and the brief is too short to '
                                   'narrate by itself. Write two or three full sentences, or try later.') from None

    async def __call__(self, prompt):
        import asyncio
        if self.design is None:
            plan, self.writer = await asyncio.to_thread(self.plan)
            self.design = design_from_plan(plan, self.writer)
        return json.dumps(self.design, ensure_ascii=False)


def choose_topic(writers, settings, history, avoid=()):
    from content import find, rules_text

    def validate(raw):
        if not all(isinstance(raw.get(k), str) and raw[k].strip() for k in ('topic', 'brief')):
            raise ValueError('Topic is incomplete')
        if find({k: raw.get(k) for k in ('topic', 'angle', 'brief')}, avoid):
            raise ValueError('Topic touches the owner\'s never list')
        return {'topic': text(raw['topic'], 200), 'angle': text(raw.get('angle', ''), 300),
                'brief': text(raw['brief'], 2000)}
    return ask(writers, topic_prompt(settings, history) + rules_text(avoid), validate)
