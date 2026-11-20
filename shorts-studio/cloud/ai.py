"""Which AI writes and edits: Claude (Claude Code) or ChatGPT (OpenAI's Codex CLI).

Both use the owner's own subscription through the official command-line tool, never an API key.
The choice is the "ai" setting in shorts-studio/autopilot/settings.json (Settings → AI on the
page). Workflows pass it on as STUDIO_AI, read when the account job ran.

Each engine knows how to start its CLI with only its own sign-in, how to ask one bounded question
(optionally with images) and how to read the final answer out of the CLI's JSON event stream.
"""
import base64
import json
import os
import re
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
SETTINGS = HERE.parent / 'autopilot' / 'settings.json'
PROVIDERS = ('claude', 'chatgpt')
NAMES = {'claude': 'Claude', 'chatgpt': 'ChatGPT'}
# The model each one writes films with (shown in film progress).
MODELS = {'claude': 'claude-opus-5-5', 'chatgpt': 'gpt-6.2-astra'}
MODEL_LABELS = {'claude': 'Opus 5.5', 'chatgpt': 'GPT-6.2 Astra'}


def provider(source=None, settings=None):
    """'claude' or 'chatgpt': STUDIO_AI from the workflow, else the saved setting, else Claude."""
    source = os.environ if source is None else source
    chosen = str(source.get('STUDIO_AI', '') or '').strip().lower()
    if chosen in PROVIDERS:
        return chosen
    if settings is None:
        try:
            settings = json.loads(Path(source.get('STUDIO_SETTINGS') or SETTINGS).read_text(encoding='utf-8'))
        except (OSError, ValueError):
            settings = {}
    chosen = settings.get('ai') if isinstance(settings, dict) else None
    return chosen if chosen in PROVIDERS else 'claude'


def name(source=None):
    return NAMES[provider(source)]


# ---------------------------------------------------------------------------------------------
# Claude Code

class Claude:
    key, name, binary = 'claude', 'Claude', 'claude'
    model, model_label = MODELS['claude'], MODEL_LABELS['claude']

    def signed_in(self, source):
        return bool(source.get('STUDIO_CLAUDE_ACCESS') or source.get('CLAUDE_CODE_OAUTH_TOKEN', '').strip())

    def env(self, home, source=None):
        """Only what Claude Code needs: never the GitHub token, YouTube credentials or API keys."""
        from claude_account import access_token
        source = os.environ if source is None else source
        token = access_token(source)
        if not token:
            raise RuntimeError('Reconnect Claude from Account in Shorts Studio before creating a film.')
        env = {k: source[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TZ') if k in source}
        env.update(HOME=str(home), CLAUDE_CODE_OAUTH_TOKEN=token, TERM='xterm-256color', NO_COLOR='1',
                   DISABLE_AUTOUPDATER='1')
        return env

    def args(self, binary, research='auto', effort='high'):
        from agent import cli_args
        return cli_args(binary, research, effort)

    def question(self, binary, prompt, images=(), research='off', effort='high', workdir=None):
        """(args, stdin) for one question; pictures go in as one stream-json user turn."""
        args = self.args(binary, research, effort)
        if not images:
            return args, prompt
        content = [{'type': 'image', 'source': {'type': 'base64', 'media_type': 'image/png',
                                                'data': base64.b64encode(image).decode()}} for image in images]
        payload = json.dumps({'type': 'user', 'message': {'role': 'user', 'content': content + [
            {'type': 'text', 'text': prompt}]}}) + '\n'
        return args + ['--input-format', 'stream-json'], payload

    def unpack(self, output):
        from agent import unpack_output
        return unpack_output(output)


# ---------------------------------------------------------------------------------------------
# ChatGPT through OpenAI's Codex CLI

# Codex is a coding agent; here it only writes text. Its shell, apps, plugins, browser and image
# tools are turned off, so a prompt can never make it run a command or reach the sign-in file.
CODEX_OFF = ('shell_tool', 'unified_exec', 'apps', 'plugins', 'remote_plugin', 'browser_use', 'browser_use_external',
             'in_app_browser', 'computer_use', 'image_generation', 'multi_agent', 'hooks', 'memories')
CODEX_EFFORT = {'low': 'low', 'medium': 'medium', 'high': 'high', 'xhigh': 'xhigh', 'max': 'xhigh'}


def codex_args(binary, research='auto', effort='high', images=()):
    """One non-interactive Codex run that reads the prompt from stdin and prints JSON events."""
    args = [binary, 'exec', '--json', '--skip-git-repo-check', '--ephemeral', '--ignore-user-config', '--ignore-rules',
            '--sandbox', 'read-only', '--model', MODELS['chatgpt'],
            '-c', 'approval_policy="never"', '-c', 'project_doc_max_bytes=0',
            '-c', 'model_reasoning_effort="' + CODEX_EFFORT.get(effort, 'high') + '"',
            '-c', 'web_search="' + ('disabled' if research == 'off' else 'live') + '"']
    for feature in CODEX_OFF:
        args += ['--disable', feature]
    for image in images:
        args.append('--image=' + str(image))
    return args


def unpack_codex(output):
    """The final answer of a Codex run, and which tools it used. Never takes intermediate text."""
    used, result, failure, notes = [], None, None, []
    for line in output.splitlines():
        try:
            event = json.loads(line)
        except ValueError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get('type')
        item = event.get('item') if isinstance(event.get('item'), dict) else {}
        if kind == 'item.completed':
            if item.get('type') == 'agent_message' and isinstance(item.get('text'), str):
                result = item['text']
            elif item.get('type') == 'web_search':
                used.append('WebSearch')
            elif item.get('type') == 'command_execution':
                used.append('Shell')
            elif item.get('type') == 'error':
                notes.append(str(item.get('message', '')))
        elif kind == 'error':
            notes.append(str(event.get('message', '')))
        elif kind in ('turn.failed', 'thread.failed'):
            error = event.get('error')
            failure = (error.get('message') if isinstance(error, dict) else error) or event.get('message') or 'failed'
    if failure is not None:
        raise RuntimeError('ChatGPT could not complete this request: ' + str(failure)[:300])
    if result is None:
        raise RuntimeError('ChatGPT did not return a completed result. Try again.' +
                           (' ' + notes[-1][:300] if notes else ''))
    if len(result) > 220000:
        raise RuntimeError('The generated film is too large. Try a shorter brief.')
    return result, {'tools': sorted(set(used)), 'toolCalls': len(used),
                    'searchUsed': 'WebSearch' in used, 'permissionsDenied': False}


class ChatGPT:
    key, name, binary = 'chatgpt', 'ChatGPT', 'codex'
    model, model_label = MODELS['chatgpt'], MODEL_LABELS['chatgpt']

    def signed_in(self, source):
        return bool(source.get('STUDIO_CHATGPT_ACCESS'))

    def env(self, home, source=None):
        """Only what Codex needs: its own sign-in file in a private home, nothing else.
        The refresh credential is never given: it stays in the serialized account job."""
        from chatgpt_account import access_token, auth_file
        source = os.environ if source is None else source
        access = access_token(source)
        if not access:
            raise RuntimeError('Reconnect ChatGPT from Account in Shorts Studio before creating a film.')
        codex_home = Path(home) / '.codex'
        codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        path = codex_home / 'auth.json'
        path.write_text(json.dumps(auth_file(access, refresh=False)), encoding='utf-8')
        path.chmod(0o600)
        env = {k: source[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TZ') if k in source}
        env.update(HOME=str(home), CODEX_HOME=str(codex_home), TERM='xterm-256color', NO_COLOR='1')
        return env

    def args(self, binary, research='auto', effort='high'):
        return codex_args(binary, research, effort)

    def question(self, binary, prompt, images=(), research='off', effort='high', workdir=None):
        """(args, stdin) for one question; pictures are saved next to the run and attached."""
        paths = []
        if images:
            folder = Path(workdir or '.') / ('pictures-' + str(time.monotonic_ns()))
            folder.mkdir(parents=True, exist_ok=True)
            for n, image in enumerate(images):
                path = folder / f'{n + 1}.png'
                path.write_bytes(image)
                paths.append(path)
        return codex_args(binary, research, effort, images=paths), prompt

    def unpack(self, output):
        return unpack_codex(output)


ENGINES = {'claude': Claude(), 'chatgpt': ChatGPT()}


def engine(source=None, settings=None):
    """The engine for the AI chosen in Settings."""
    return ENGINES[provider(source, settings)]


def engine_for(binary):
    """The engine that runs this CLI binary (codex is ChatGPT; anything else is Claude Code)."""
    base = re.sub(r'\.(exe|cmd|js)$', '', Path(str(binary or '')).name.lower())
    return ENGINES['chatgpt'] if base == 'codex' else ENGINES['claude']
