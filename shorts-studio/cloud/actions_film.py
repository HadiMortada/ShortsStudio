"""Create one Claude-authored film on a GitHub Actions runner, without Modal or a VPS.

Claude Code runs with the owner's subscription token (CLAUDE_CODE_OAUTH_TOKEN, from
`claude setup-token`). The same ProductionManager used on Modal writes the animation,
records edge-tts narration, renders with Remotion, repairs once and reviews six frames.

Generated TSX renders in a separate process that receives no secrets. With
STUDIO_RENDER_SANDBOX=netns it also runs in a network namespace with only loopback,
matching the Modal sandbox's blocked network.

Publishing is not done here: the deliver job uploads the finished film with its own secret.
"""
import argparse
import asyncio
import hashlib
import json
import os
import re
import shutil
import signal
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from agent import REVIEW_UNAVAILABLE, cli_args, review_message, review_result, unpack_output  # noqa: E402
from production import VOICES, ProductionManager, RenderFailure, parse_design  # noqa: E402
from content import avoid_list, find as blocked_in, rules_text  # noqa: E402
from slots import publish_at  # noqa: E402

DELIVERABLES = ('video.mp4', 'video.png', 'design.json', 'sources.json', 'topic.json',
                'quality.json', 'research.json', 'status.json')


DESIGN_FIELDS = ('title', 'direction', 'language', 'narration', 'source', 'description', 'sources', 'assets',
                 'beats', 'youtube')


def saved_film(folder):
    """The film Claude already wrote in an earlier try at this post time: (design, topic) or None.
    A retry reuses it, so only the steps that failed (voice, render) run again and Claude is not used."""
    if not folder:
        return None
    try:
        design = json.loads((Path(folder) / 'design.json').read_text(encoding='utf-8'))
        design = {k: design[k] for k in DESIGN_FIELDS if k in design}
        parse_design(json.dumps(design))
    except (OSError, ValueError, TypeError, AttributeError):
        return None
    try:
        topic = json.loads((Path(folder) / 'topic.json').read_text(encoding='utf-8'))
        topic = topic if isinstance(topic, dict) and str(topic.get('brief', '')).strip() else None
    except (OSError, ValueError):
        topic = None
    return design, topic


class SavedAuthor:
    """Answers with the saved film instead of asking Claude."""
    failure, audit = '', {}

    def __init__(self, design):
        self.text = json.dumps(design, ensure_ascii=False)

    async def __call__(self, prompt):
        return self.text


def job_id_for(seed):
    """Stable per workflow run, so re-running the deliver job finds the same upload receipt."""
    return hashlib.sha256(seed.encode()).hexdigest()[:32]


async def run_process(args, *, env, stdin=None, timeout=180, cwd=None):
    process = await asyncio.create_subprocess_exec(
        *args, env=env, cwd=cwd, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, start_new_session=True)
    try:
        out, err = await asyncio.wait_for(process.communicate(stdin.encode() if stdin else None), timeout)
        return process.returncode, out.decode(errors='replace'), err.decode(errors='replace')
    finally:
        if process.returncode is None:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            await process.wait()


def claude_env(home, source=None):
    """Only what Claude Code needs: never the GitHub token, YouTube credentials or API keys."""
    source = os.environ if source is None else source
    token = source.get('CLAUDE_CODE_OAUTH_TOKEN', '').strip()
    if not token:
        raise RuntimeError('Add the CLAUDE_CODE_OAUTH_TOKEN repository secret. '
                           'Create it with `claude setup-token` on a computer signed in to Claude.')
    env = {k: source[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TZ') if k in source}
    env.update(HOME=str(home), CLAUDE_CODE_OAUTH_TOKEN=token, TERM='xterm-256color', NO_COLOR='1',
               DISABLE_AUTOUPDATER='1')
    return env


LIMIT_SIGNS = ('usage limit', 'limit reached', 'rate limit', 'rate_limit', 'quota', 'too many requests', '429')
SIGNIN_SIGNS = ('oauth', 'unauthorized', '401', 'authentication', 'invalid api key', 'invalid bearer', 'log in',
                'login', 'expired', 'revoked', 'credential', 'not logged')


def claude_issue(text):
    """'limit' (usage limit; resets by itself), 'signin' (token expired or revoked) or ''."""
    text = (text or '').lower()
    if any(sign in text for sign in LIMIT_SIGNS):
        return 'limit'
    if any(sign in text for sign in SIGNIN_SIGNS):
        return 'signin'
    return ''


def make_author(binary, env, research, run=run_process, timeout=900):
    async def author(prompt):
        code, out, err = await run(cli_args(binary, research), env=env, stdin=prompt, timeout=timeout)
        try:
            if code:
                raise RuntimeError('exit ' + str(code))
            result, author.audit = unpack_output(out)
            return result
        except RuntimeError:
            author.failure = claude_issue(out[-4000:] + '\n' + err[-4000:]) or author.failure
            print('Claude Code did not finish (' + (author.failure or 'unknown reason') + ')', err[-1500:],
                  file=sys.stderr)
            if author.failure == 'signin':
                raise RuntimeError('Claude sign-in expired or was revoked. Renew it from the Shorts Studio page.') from None
            if author.failure == 'limit':
                raise RuntimeError('Claude usage limit reached. It resets automatically.') from None
            raise RuntimeError('Claude could not create the film. Check the CLAUDE_CODE_OAUTH_TOKEN '
                               'secret and your subscription usage limits.') from None
    author.audit, author.failure = {}, ''
    return author


def make_review(binary, env, run=run_process):
    async def review(design, contact_sheet):
        try:
            code, out, _ = await run(cli_args(binary, 'off') + ['--input-format', 'stream-json'],
                                     env=env, stdin=review_message(design, contact_sheet), timeout=300)
        except Exception:
            return dict(REVIEW_UNAVAILABLE)
        # A failed reviewer must never approve unattended publication.
        return dict(REVIEW_UNAVAILABLE) if code else review_result(out)
    return review


class LocalRenderer:
    """Runs render/render.mjs from a directory holding the pinned Remotion node_modules."""

    def __init__(self, workdir, sandbox='', timeout=1200, source=None):
        self.workdir = Path(workdir).resolve()
        self.sandbox, self.timeout = sandbox, timeout
        self.source = os.environ if source is None else source
        if not (self.workdir / 'runner' / 'render.mjs').is_file():
            raise RuntimeError('Renderer is not prepared: ' + str(self.workdir / 'runner' / 'render.mjs'))

    def environment(self, job):
        env = {k: self.source[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TZ') if k in self.source}
        env.update(HOME=str(job.parent / 'home'), STUDIO_RENDER_ROOT=str(job),
                   STUDIO_CHROMIUM=self.source.get('STUDIO_CHROMIUM', ''), NO_COLOR='1')
        return env

    def command(self, job, env):
        node = shutil.which('node', path=env.get('PATH')) or 'node'
        script = str(self.workdir / 'runner' / 'render.mjs')
        if self.sandbox != 'netns':
            return [node, script], env
        settings = job.parent / 'isolated-env.json'
        settings.write_text(json.dumps(env))
        wrapper = ['unshare', '--net', '--', sys.executable, '-I', str(HERE / 'isolate.py'),
                   str(os.getuid()), str(os.getgid()), str(settings), node, script]
        return (wrapper if os.geteuid() == 0 else ['sudo', '-n'] + wrapper), {'PATH': env.get('PATH', '')}

    def __call__(self, image, design, audio, seconds, destination):
        import subprocess
        job = self.workdir / 'job'
        shutil.rmtree(job, ignore_errors=True)
        (job / 'public').mkdir(parents=True)
        (self.workdir / 'home').mkdir(exist_ok=True)
        (job / 'Video.tsx').write_text(design['source'], encoding='utf-8')
        shutil.copyfile(audio, job / 'public' / 'narration.mp3')
        assets = Path(destination).parent / 'assets'
        if assets.is_dir():
            (job / 'public' / 'assets').mkdir()
            for asset in assets.iterdir():
                if asset.is_file() and re.fullmatch(r'[a-zA-Z0-9_]+\.(?:png|mp4)', asset.name):
                    shutil.copyfile(asset, job / 'public' / 'assets' / asset.name)
        from production import render_config
        (job / 'config.json').write_text(json.dumps(render_config(design, seconds)))
        args, env = self.command(job, self.environment(job))
        try:
            done = subprocess.run(args, env=env, cwd=self.workdir, capture_output=True,
                                  text=True, errors='replace', timeout=self.timeout)
        except subprocess.TimeoutExpired:
            raise RenderFailure('Rendering exceeded the time limit') from None
        if done.returncode != 0:
            raise RenderFailure((done.stderr or done.stdout or 'Renderer did not complete')[-5000:])
        video = job / 'video.mp4'
        if not video.is_file() or not 1000 < video.stat().st_size < 150_000_000:
            raise RenderFailure('Rendered video size is invalid')
        destination = Path(destination)
        shutil.copyfile(video, destination)
        shutil.copyfile(job / 'preview.png', destination.with_suffix('.png'))
        shutil.copyfile(job / 'metrics.json', destination.with_suffix('.metrics.json'))
        shutil.copyfile(job / 'review.png', destination.parent / 'review.png')


class HistoryStore:
    """The Automation store, backed by a JSON list committed to the repository."""

    def __init__(self, entries):
        self.data = {'history': list(entries)}

    def get(self, key, default=None):
        return self.data.get(key, default)

    def __setitem__(self, key, value):
        self.data[key] = value


def autopilot_request(config_path, history_path, scheduled, now=None, slot=''):
    """Returns (brief, seconds, voice, options, store) or a skip reason string."""
    from zoneinfo import ZoneInfo
    from autonomy import Automation, Schedule
    try:
        raw = json.loads(Path(config_path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return 'No autopilot settings file; nothing to create.'
    config = Schedule.model_validate(raw)
    if (scheduled or slot) and not config.enabled:
        return 'Autopilot is disabled in ' + str(config_path) + '.'
    if not config.domain.strip():
        raise RuntimeError('Set "domain" in ' + str(config_path) + ' before running autopilot.')
    if config.voice not in VOICES:
        raise RuntimeError('Select a supported narrator in ' + str(config_path) + '.')
    moment = datetime.fromtimestamp(now if now is not None else time.time(), timezone.utc)
    if scheduled and moment.astimezone(ZoneInfo(config.timezone)).weekday() not in config.days:
        return 'Today is not a selected autopilot day.'
    try:
        history = json.loads(Path(history_path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        history = []
    if not isinstance(history, list):
        raise RuntimeError('Autopilot history must be a JSON list.')
    store = HistoryStore(history)
    settings = config.model_dump()
    settings.pop('resources', None)  # Reference folders live on the Modal service only.
    options = {'research': 'auto', 'autonomous': settings, 'publish': config.publish, 'privacy': config.privacy,
               'writer': config.writer, 'slot': slot}
    return config.domain, config.seconds, config.voice, options, Automation(store)


def next_idea(path, avoid=()):
    """The top idea of the Up next list, skipping any that touch the owner's never list."""
    try:
        ideas = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return None
    for idea in ideas if isinstance(ideas, list) else []:
        if isinstance(idea, dict) and str(idea.get('topic', '')).strip() \
                and not blocked_in({k: idea.get(k) for k in ('topic', 'angle', 'brief')}, avoid):
            return idea
    return None


def summary_lines(state, design, quality, delivery):
    writer = 'Claude' if delivery.get('writer') == 'claude' else 'Free backup — ' + (delivery.get('writerDetail') or '')
    lines = ['## ' + (design.get('title') or 'Shorts Studio film'), '',
             '**State:** ' + str(state.get('state', 'unknown')) + ' — ' + state.get('message', ''),
             '**Written by:** ' + writer]
    if delivery.get('fallbackReason'):
        lines.append('**Why the backup was used:** ' + delivery['fallbackReason'])
    if quality:
        lines.append('**Visual check:** ' + ('passed' if quality.get('passed') else 'needs attention')
                     + ' (' + quality.get('method', '') + ')')
        lines += ['- ' + x for x in quality.get('issues', [])]
    if delivery.get('publish'):
        lines.append('**YouTube:** ' + ('will upload as ' + delivery['privacy'] if delivery['qualityPassed']
                     else 'held — preview the film and publish manually'))
    elif delivery.get('heldReason'):
        lines.append('**YouTube:** ' + delivery['heldReason'])
    lines.append('')
    lines.append('Watch it in your Shorts Studio page, or download the MP4 from this run\'s **Artifacts**.')
    return '\n'.join(lines) + '\n'


async def produce(root, author, review, render, brief, seconds, voice, job_id, options, automation, out, overrides):
    for name in DELIVERABLES:
        (out / name).unlink(missing_ok=True)
    # Uploading is the deliver job's responsibility, so the manager never publishes here.
    manager = ProductionManager(root, author, None, render=render, review=review,
                                automation=automation, **overrides)
    manager.start(brief, seconds, voice, job_id=job_id, options={**options, 'publish': False})
    await manager.task
    folder = manager.root / job_id
    for name in DELIVERABLES:
        if (folder / name).is_file():
            shutil.copyfile(folder / name, out / name)
    return manager.status(job_id)


async def create(args, *, author, review, render, writers=None, now=None, **overrides):
    """Claude first; the free backup writer takes over whenever Claude cannot finish.
    overrides (synth, probe) are passed to ProductionManager; tests replace narration."""
    from backup import BackupAuthor, WriterUnavailable, choose_topic, writers_from_env
    writers = writers_from_env() if writers is None else writers
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    automation = idea = settings = saved = None
    if args.brief.strip():
        brief, seconds, voice = args.brief.strip(), args.seconds, args.voice
        options = {'research': args.research, 'publish': args.publish, 'privacy': args.privacy}
    else:
        request = autopilot_request(args.autopilot, args.history, args.scheduled, now=now, slot=args.slot)
        if isinstance(request, str):
            (out / 'delivery.json').write_text(json.dumps({'state': 'skipped', 'message': request}))
            return {'state': 'skipped', 'message': request}
        brief, seconds, voice, options, automation = request
        try:
            avoid = avoid_list(json.loads(Path(args.autopilot).read_text(encoding='utf-8')))
        except (OSError, ValueError):
            avoid = avoid_list({})
        idea = next_idea(args.ideas, avoid)
        if idea:
            # The owner's "Up next" list: make its top idea instead of researching a new topic.
            topic = {'topic': idea['topic'], 'angle': idea.get('angle', ''), 'brief': idea.get('brief') or idea['topic'],
                     'reason': idea.get('reason') or 'From your Up next list', 'evidence': idea.get('evidence', [])}
            brief = topic['brief']
        saved = saved_film(args.previous) if args.slot and args.writer != 'backup' else None
        if saved:
            if not idea:
                design_s, topic_s = saved
                topic = topic_s or {'topic': design_s['title'], 'angle': '', 'brief': design_s['title'],
                                    'reason': 'Chosen by Claude in the first try at this post time', 'evidence': []}
            brief = topic['brief']
            print('Reusing the film Claude wrote in the first try at this post time; Claude is not used.',
                  file=sys.stderr, flush=True)
    if voice not in VOICES:
        raise RuntimeError('Select a supported narrator')
    if not 15 <= seconds <= 60:
        raise RuntimeError('Choose a film length from 15 to 60 seconds')
    if options['privacy'] not in ('private', 'unlisted', 'public'):
        raise RuntimeError('Invalid YouTube visibility')
    try:
        studio = json.loads(Path(args.autopilot).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        studio = {}
    options['captions'] = bool(studio.get('captions', True)) if isinstance(studio, dict) else True
    options['endCard'] = bool(studio.get('endCard', True)) if isinstance(studio, dict) else True
    options['avoid'] = avoid_list(studio)
    settings = options.get('autonomous') or {}
    if idea or saved:
        options = {k: v for k, v in options.items() if k != 'autonomous'}
    writer = options.pop('writer', None) or args.writer
    slot = options.pop('slot', '') or ''
    if saved:
        author, review = SavedAuthor(saved[0]), None
    if writer == 'claude' and author is None:
        message = ('Claude is not connected, and this film was set to Claude only. Open your Shorts Studio page, '
                   'go to Account and tap Sign in with Claude.')
        (out / 'delivery.json').write_text(json.dumps({'state': 'failed', 'message': message, 'claudeIssue': 'signin'}))
        return {'state': 'failed', 'message': message, 'claudeIssue': 'signin'}
    job_id = job_id_for(args.seed)
    used, detail, fallback, state, issue = None, '', '', {}, ''
    topic = topic if idea or saved else None
    with tempfile.TemporaryDirectory(prefix='studio-') as root:
        common = dict(render=render, seconds=seconds, voice=voice, job_id=job_id, out=out, overrides=overrides)
        if author is not None and writer in ('auto', 'claude'):
            state = await produce(Path(root) / 'claude', author, review, brief=brief, options=options,
                                  automation=automation, **common)
            used = 'claude'
            if saved:
                detail = 'reused from the first try at this post time'
            issue = getattr(author, 'failure', '') if state.get('state') != 'ready' else ''
            if state.get('state') != 'ready' and writer == 'auto':
                fallback = 'Claude could not finish: ' + state.get('message', 'unknown error')
        elif writer == 'auto':
            fallback = 'The Claude sign-in token is not configured.'
            issue = 'signin'
        if writer == 'backup' or fallback:
            print('Using the free backup writer. ' + fallback, file=sys.stderr, flush=True)
            backup_brief = brief
            if automation is not None and not (idea or saved):
                try:
                    topic, _ = await asyncio.to_thread(choose_topic, writers, options['autonomous'], automation.history(),
                                                        options['avoid'])
                    backup_brief = topic['brief']
                except WriterUnavailable:
                    state = {'state': 'failed', 'message': 'No writer was available to choose a topic today. '
                             'Autopilot will try again at the next scheduled time.'}
            if automation is None or topic:
                author_b = BackupAuthor(backup_brief, seconds, writers, allow_brief_plan=automation is None,
                                        rules=rules_text(options['avoid']))
                state = await produce(Path(root) / 'backup', author_b, None, brief=backup_brief,
                                      options={k: v for k, v in options.items() if k != 'autonomous'},
                                      automation=None, **common)
                used, detail = 'backup', author_b.writer or ''
                if state.get('state') == 'ready':
                    state['quality'] = {'passed': True, 'issues': [],
                                        'method': 'Template film from the backup writer; render validated'}
                    if topic and not (idea or saved):
                        (out / 'topic.json').write_text(json.dumps(topic, ensure_ascii=False))
                        automation.remember({'jobId': job_id, **topic, 'reason': 'Chosen by the backup writer '
                            'without live research', 'evidence': [], 'createdAt': time.time(), 'state': 'ready',
                            'writer': 'backup'})
    design = json.loads((out / 'design.json').read_text()) if (out / 'design.json').is_file() else {}
    quality = state.get('quality') or {}
    ready = state.get('state') == 'ready' and (out / 'video.mp4').is_file()
    publish, held = bool(options.get('publish')) and ready, ''
    if publish and used == 'backup' and automation is not None and not settings.get('publishBackup'):
        publish, held = False, 'not uploaded automatically: backup films need "publishBackup": true in autopilot settings.'
    delivery = {'state': state.get('state'), 'jobId': job_id, 'message': state.get('message', ''),
                'publish': publish, 'privacy': options['privacy'], 'heldReason': held,
                'qualityPassed': bool(quality.get('passed')), 'autonomous': automation is not None,
                'writer': used, 'writerDetail': detail, 'fallbackReason': fallback, 'claudeIssue': issue,
                'title': design.get('title', ''), 'description': design.get('description', ''),
                'seconds': state.get('seconds'), 'brief': (topic or {}).get('brief', brief)[:400],
                'createdAt': int(time.time()), 'slot': slot, 'ideaId': (idea or {}).get('id') if ready else None,
                # YouTube publishes a private upload at this time; only public posts are scheduled.
                'publishAt': publish_at(slot, datetime.fromtimestamp(now, timezone.utc) if now else None)
                             if publish and options['privacy'] == 'public' else None}
    if automation is not None and (idea or saved) and ready:
        automation.remember({'jobId': job_id, **topic, 'createdAt': time.time(), 'state': 'ready', 'writer': used})
        (out / 'topic.json').write_text(json.dumps(topic, ensure_ascii=False))
    if automation is not None:
        entries = automation.history()
        if slot and ready:
            # Claim the post time so the hourly check never makes it twice.
            entries = [{**x, 'slot': slot} if x.get('jobId') == job_id else x for x in entries]
            if not any(x.get('jobId') == job_id for x in entries):
                entries.append({'jobId': job_id, 'slot': slot, 'createdAt': time.time(), 'state': 'ready', 'writer': used})
            automation.store['history'] = entries[-90:]
        (out / 'history.json').write_text(json.dumps(automation.history(), ensure_ascii=False, indent=1))
    (out / 'delivery.json').write_text(json.dumps(delivery, ensure_ascii=False))
    summary = os.environ.get('GITHUB_STEP_SUMMARY')
    if summary:
        with open(summary, 'a', encoding='utf-8') as stream:
            stream.write(summary_lines(state, design, quality, delivery))
    return delivery


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--brief', default='', help='Creative brief; empty uses the autopilot settings')
    parser.add_argument('--seconds', type=int, default=30)
    parser.add_argument('--voice', default='auto')
    parser.add_argument('--research', choices=('auto', 'off'), default='auto')
    parser.add_argument('--writer', choices=('auto', 'claude', 'backup'), default='auto',
                        help='auto: Claude, then the free backup if Claude cannot finish')
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--privacy', default='private')
    parser.add_argument('--autopilot', default='shorts-studio/autopilot/settings.json')
    parser.add_argument('--history', default='shorts-studio/autopilot/history.json')
    parser.add_argument('--scheduled', action='store_true', help='Honor "enabled" and "days"')
    parser.add_argument('--slot', default='', help='Post time chosen by the hourly autopilot check (ISO)')
    parser.add_argument('--previous', default='', help="An earlier failed try's film files, reused instead of Claude")
    parser.add_argument('--ideas', default='shorts-studio/autopilot/ideas.json', help='The Up next topic list')
    parser.add_argument('--renderer', required=True, help='Directory with node_modules and runner/render.mjs')
    parser.add_argument('--out', required=True)
    parser.add_argument('--seed', default='', help='Unique run identifier (GitHub run ID)')
    args = parser.parse_args(argv)
    if not args.seed:
        args.seed = os.environ.get('GITHUB_REPOSITORY', 'local') + ':' + os.environ.get('GITHUB_RUN_ID', str(time.time()))
    return args


def main(argv=None):
    args = parse_args(argv)
    binary = shutil.which('claude')
    signed_in = bool(os.environ.get('CLAUDE_CODE_OAUTH_TOKEN', '').strip())
    render = LocalRenderer(args.renderer, sandbox=os.environ.get('STUDIO_RENDER_SANDBOX', ''))
    with tempfile.TemporaryDirectory(prefix='claude-home-') as home:
        author = review = None
        if args.writer != 'backup' and signed_in and binary:
            env = claude_env(home)
            # Autopilot topic selection always needs live web research.
            research = args.research if args.brief.strip() else 'auto'
            author, review = make_author(binary, env, research), make_review(binary, env)
        result = asyncio.run(create(args, author=author, review=review, render=render))
    print(json.dumps(result, ensure_ascii=False))
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a') as stream:
            stream.write('state=' + str(result.get('state')) + '\n')
            stream.write('deliver=' + str(result.get('state') == 'ready').lower() + '\n')
    from notify import notify
    if result.get('claudeIssue') == 'signin':
        notify('signin', 'Claude sign-in expired', 'Films use the free backup until you renew. Tap to renew.', '/#account')
    if result.get('state') == 'failed':
        notify('failed', 'A film did not finish', result.get('message', '')[:300], '/#create')
        print('::error::' + result.get('message', 'Film creation failed'))
        sys.exit(1)


if __name__ == '__main__':
    main()
