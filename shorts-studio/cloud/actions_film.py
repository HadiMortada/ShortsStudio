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
from production import VOICES, ProductionManager, RenderFailure  # noqa: E402

DELIVERABLES = ('video.mp4', 'video.png', 'design.json', 'sources.json', 'topic.json',
                'quality.json', 'research.json', 'status.json')


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


def make_author(binary, env, research, run=run_process, timeout=900):
    async def author(prompt):
        code, out, err = await run(cli_args(binary, research), env=env, stdin=prompt, timeout=timeout)
        if code:
            print('Claude Code exited with', code, err[-2000:], file=sys.stderr)
            raise RuntimeError('Claude could not create the film. Check the CLAUDE_CODE_OAUTH_TOKEN '
                               'secret and your subscription usage limits.')
        result, author.audit = unpack_output(out)
        return result
    author.audit = {}
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
        (job / 'config.json').write_text(json.dumps({'seconds': seconds}))
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


def autopilot_request(config_path, history_path, scheduled, now=None):
    """Returns (brief, seconds, voice, options, store) or a skip reason string."""
    from zoneinfo import ZoneInfo
    from autonomy import Automation, Schedule
    try:
        raw = json.loads(Path(config_path).read_text(encoding='utf-8'))
    except FileNotFoundError:
        return 'No autopilot settings file; nothing to create.'
    config = Schedule.model_validate(raw)
    if scheduled and not config.enabled:
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
    options = {'research': 'auto', 'autonomous': settings, 'publish': config.publish, 'privacy': config.privacy}
    return config.domain, config.seconds, config.voice, options, Automation(store)


def summary_lines(state, design, quality, delivery):
    lines = ['## ' + (design.get('title') or 'Shorts Studio film'), '',
             '**State:** ' + state.get('state', 'unknown') + ' — ' + state.get('message', '')]
    if quality:
        lines.append('**Visual review:** ' + ('passed' if quality.get('passed') else 'needs attention')
                     + ' (' + quality.get('method', '') + ')')
        lines += ['- ' + x for x in quality.get('issues', [])]
    if delivery.get('publish'):
        lines.append('**YouTube:** ' + ('will upload as ' + delivery['privacy'] if delivery['qualityPassed']
                     else 'held — preview the film and publish manually'))
    lines.append('')
    lines.append('Download the MP4 from this run\'s **Artifacts**.')
    return '\n'.join(lines) + '\n'


async def create(args, *, author, review, render, now=None, **overrides):
    """overrides (synth, probe) are passed to ProductionManager; tests replace narration."""
    out = Path(args.out).resolve()
    out.mkdir(parents=True, exist_ok=True)
    automation = None
    if args.brief.strip():
        brief, seconds, voice = args.brief.strip(), args.seconds, args.voice
        options = {'research': args.research, 'publish': args.publish, 'privacy': args.privacy}
    else:
        request = autopilot_request(args.autopilot, args.history, args.scheduled, now=now)
        if isinstance(request, str):
            (out / 'delivery.json').write_text(json.dumps({'state': 'skipped', 'message': request}))
            return {'state': 'skipped', 'message': request}
        brief, seconds, voice, options, automation = request
    if voice not in VOICES:
        raise RuntimeError('Select a supported narrator')
    if not 15 <= seconds <= 60:
        raise RuntimeError('Choose a film length from 15 to 60 seconds')
    if options['privacy'] not in ('private', 'unlisted', 'public'):
        raise RuntimeError('Invalid YouTube visibility')
    job_id = job_id_for(args.seed)
    with tempfile.TemporaryDirectory(prefix='studio-') as root:
        # Uploading is the deliver job's responsibility, so the manager never publishes here.
        manager = ProductionManager(root, author, None, render=render, review=review,
                                    automation=automation, **overrides)
        manager.start(brief, seconds, voice, job_id=job_id, options={**options, 'publish': False})
        await manager.task
        state = manager.status(job_id)
        folder = manager.root / job_id
        for name in DELIVERABLES:
            if (folder / name).is_file():
                shutil.copyfile(folder / name, out / name)
    design = json.loads((out / 'design.json').read_text()) if (out / 'design.json').is_file() else {}
    quality = state.get('quality') or {}
    ready = state.get('state') == 'ready' and (out / 'video.mp4').is_file()
    delivery = {'state': state.get('state'), 'jobId': job_id, 'message': state.get('message', ''),
                'publish': bool(options.get('publish')) and ready, 'privacy': options['privacy'],
                'qualityPassed': bool(quality.get('passed')), 'autonomous': automation is not None}
    if automation is not None:
        (out / 'history.json').write_text(json.dumps(automation.history(), ensure_ascii=False, indent=1))
    (out / 'delivery.json').write_text(json.dumps(delivery))
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
    parser.add_argument('--publish', action='store_true')
    parser.add_argument('--privacy', default='private')
    parser.add_argument('--autopilot', default='shorts-studio/autopilot/settings.json')
    parser.add_argument('--history', default='shorts-studio/autopilot/history.json')
    parser.add_argument('--scheduled', action='store_true', help='Honor "enabled" and "days"')
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
    if not binary:
        raise SystemExit('Claude Code is not installed on this runner.')
    with tempfile.TemporaryDirectory(prefix='claude-home-') as home:
        env = claude_env(home)
        # Autopilot topic selection always needs live web research.
        research = args.research if args.brief.strip() else 'auto'
        render = LocalRenderer(args.renderer, sandbox=os.environ.get('STUDIO_RENDER_SANDBOX', ''))
        result = asyncio.run(create(args, author=make_author(binary, env, research),
                                    review=make_review(binary, env), render=render))
    print(json.dumps(result))
    output = os.environ.get('GITHUB_OUTPUT')
    if output:
        with open(output, 'a') as stream:
            stream.write('state=' + str(result.get('state')) + '\n')
            stream.write('deliver=' + str(result.get('state') == 'ready'
                         and (result.get('publish') or result.get('autonomous'))).lower() + '\n')
    if result.get('state') == 'failed':
        print('::error::' + result.get('message', 'Film creation failed'))
        sys.exit(1)


if __name__ == '__main__':
    main()
