"""Connect the owner's ChatGPT account from the Shorts Studio phone page.

Runs the official `codex login --device-auth` on the GitHub runner. The page shows OpenAI's
sign-in link and a one-time code; the owner opens the link on the phone, signs in, types the code
and approves. Codex then saves its sign-in, which goes straight to encrypted GitHub storage. The
phone never receives an access or refresh credential; only the link, the code and the status
travel through the sign-in mailbox (a repository variable).

Device code sign-in must be allowed once in ChatGPT: Settings → Security → "Device code
authorization for Codex" (workspace admins allow it for a workspace).
"""
import os
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from urllib.parse import urlsplit

from chatgpt_account import account_credentials, save_account
from claude_login import ANSI, Variables

STATE = 'STUDIO_LOGIN_STATE'
URL = re.compile(r'https://[^\s\'"<>]+')
USER_CODE = re.compile(r'\b([A-Z0-9]{4,5}-[A-Z0-9]{4,6})\b')
NOT_ALLOWED = re.compile(r'device code (?:login|auth\w*) (?:is )?(?:not|disabled)|enable device code|not enabled', re.I)


def log(message):
    print('[chatgpt-login] ' + message, flush=True)


def sign_in_link(text):
    """OpenAI's device sign-in page and the one-time code, once Codex has printed both."""
    url = next((u.rstrip('.,);') for u in URL.findall(text)
                if (urlsplit(u).hostname or '') in ('auth.openai.com', 'chatgpt.com', 'auth.chatgpt.com')), None)
    code = USER_CODE.search(text)
    return (url, code.group(1)) if url and code else None


class DeviceLogin:
    """`codex login --device-auth` in a private Codex home."""

    def __init__(self, binary, home):
        self.home = Path(home)
        self.codex_home = self.home / '.codex'
        self.codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': str(self.home), 'CODEX_HOME': str(self.codex_home),
               'LANG': 'C.UTF-8', 'NO_COLOR': '1', 'TERM': 'dumb', 'BROWSER': '/bin/true'}
        self.process = subprocess.Popen([binary, 'login', '--device-auth'], stdin=subprocess.DEVNULL,
                                        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env, cwd=str(self.home),
                                        start_new_session=True)
        self.buffer = b''

    def read(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self.process.stdout], [], [], 0.5)
            if ready:
                chunk = os.read(self.process.stdout.fileno(), 65536)
                if not chunk:
                    break
                self.buffer += chunk
            elif self.process.poll() is not None:
                break
        return ANSI.sub('', self.buffer.decode('utf-8', errors='replace')).replace('\r', '')

    def wait_for(self, find, seconds):
        end = time.time() + seconds
        while time.time() < end:
            found = find(self.read(1))
            if found:
                return found
            if self.process.poll() is not None:
                return find(self.read(0.5))
        return None

    def finished(self, seconds):
        """True when Codex saved the sign-in, False when it stopped without, None while waiting."""
        self.read(seconds)
        code = self.process.poll()
        return None if code is None else code == 0

    def credentials(self):
        try:
            return account_credentials((self.codex_home / 'auth.json').read_text(encoding='utf-8'))
        except (OSError, ValueError, RuntimeError):
            raise RuntimeError('ChatGPT did not save a usable sign-in. Sign in again.') from None

    def close(self):
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        self.process.wait()


def connect(nonce, variables, save, login_factory, wait=15 * 60, poll=2.0):
    def state(value, message='', **extra):
        variables.set(STATE, {'nonce': nonce, 'provider': 'chatgpt', 'state': value, 'message': message,
                              'updatedAt': int(time.time()), **extra})

    login = None
    try:
        state('starting', 'Starting the official ChatGPT sign-in…')
        login = login_factory()
        found = login.wait_for(sign_in_link, 90)
        if not found:
            text = login.read(0)
            if NOT_ALLOWED.search(text):
                raise RuntimeError('Turn on device code sign-in first: ChatGPT → Settings → Security → '
                                   '"Device code authorization for Codex". Then try again.')
            raise RuntimeError('ChatGPT did not show a sign-in code. Try again in a minute.')
        url, code = found
        state('link', 'Open the link, sign in, enter the code and approve.', url=url, userCode=code)
        log('sign-in link and code published; waiting for approval')
        end, done = time.time() + wait, None
        while time.time() < end and done is None:
            done = login.finished(poll)
        if done is None:
            raise RuntimeError('The sign-in was not approved within 15 minutes. Start again.')
        if not done:
            if NOT_ALLOWED.search(login.read(0)):
                raise RuntimeError('Turn on device code sign-in first: ChatGPT → Settings → Security → '
                                   '"Device code authorization for Codex". Then try again.')
            raise RuntimeError('ChatGPT did not accept the sign-in. Start again and enter the newest code.')
        account = login.credentials()
        state('saving', 'Saving your ChatGPT sign-in for video creation and usage checks…')
        save(account)
        state('done', 'ChatGPT connected. Usage checks are enabled and the sign-in renews automatically.')
        log('signed in')
        return True
    except Exception as error:
        message = str(error) if isinstance(error, RuntimeError) else 'The sign-in stopped unexpectedly. Try again.'
        log('failed: ' + message)
        try:
            state('failed', message)
        except Exception:
            pass
        return False
    finally:
        if login:
            login.close()


def main():
    nonce = os.environ.get('LOGIN_NONCE', '')
    if not re.fullmatch(r'[0-9a-f]{16,64}', nonce):
        raise SystemExit('Invalid sign-in request')
    repo, token = os.environ['GITHUB_REPOSITORY'], os.environ.get('STUDIO_GITHUB_TOKEN', '')
    if not token:
        raise SystemExit('Add the STUDIO_GITHUB_TOKEN secret first.')
    binary = shutil.which('codex')
    if not binary:
        raise SystemExit('The Codex CLI is not installed')
    variables = Variables(repo, token)
    with tempfile.TemporaryDirectory(prefix='codex-login-') as home:
        ok = connect(nonce, variables, lambda value: save_account(repo, token, value), lambda: DeviceLogin(binary, home))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
