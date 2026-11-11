"""Renew the Claude subscription token from the Shorts Studio phone page.

Runs the official `claude setup-token` in a pseudo-terminal on the owner's GitHub runner:

1. The sign-in link it prints is published to the page through a repository variable.
2. The owner signs in on claude.com and pastes the one-time code into the page; the page
   writes it to a second variable, which this script reads, deletes and types into the CLI.
3. The new 1-year token is saved straight into the CLAUDE_CODE_OAUTH_TOKEN secret.

The token is never printed, logged or sent to the page. The code alone is useless: the PKCE
verifier stays inside this CLI process. Needs STUDIO_GITHUB_TOKEN with Variables and Secrets
(read and write) permissions.
"""
import fcntl
import json
import os
import pty
import re
import select
import shutil
import signal
import struct
import subprocess
import sys
import tempfile
import termios
import time
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import Request, urlopen

STATE, CODE = 'STUDIO_LOGIN_STATE', 'STUDIO_LOGIN_CODE'
SECRET = 'CLAUDE_CODE_OAUTH_TOKEN'
ANSI = re.compile(r'\x1b\[[0-9;?]*[ -/]*[@-~]|\x1b\][^\x07\x1b]*(?:\x07|\x1b\\)|\x1b[()][A-Za-z0-9]|\x1b[=>]')
TOKEN = re.compile(r'sk-ant-oat\d{2}-[A-Za-z0-9_-]{20,}')
CODE_SHAPE = re.compile(r'[A-Za-z0-9_\-#.~]{10,512}')


def log(message):
    print('[claude-login] ' + message, flush=True)


def clean(raw):
    return ANSI.sub('', raw.decode('utf-8', errors='replace')).replace('\r', '')


def sign_in_url(text):
    """The CLI may wrap the link; join it back up to the next blank line."""
    start = text.find('https://claude.')
    if start < 0:
        return None
    joined = ''
    for line in text[start:].split('\n'):
        if not line.strip():
            break
        joined += line.strip()
    url = joined.split()[0] if joined else ''
    parts = urlsplit(url)
    if parts.scheme == 'https' and parts.hostname in ('claude.com', 'claude.ai') and 'oauth/authorize' in parts.path:
        return url
    return None


class Variables:
    """GitHub Actions repository variables used as a small private mailbox."""

    def __init__(self, repo, token, call=None):
        self.repo, self.token = repo, token
        self.call = call or self.request

    def request(self, method, path, body=None):
        request = Request('https://api.github.com/repos/' + self.repo + '/actions/variables' + path,
                          data=json.dumps(body).encode() if body is not None else None, method=method,
                          headers={'Authorization': 'Bearer ' + self.token, 'Accept': 'application/vnd.github+json',
                                   'X-GitHub-Api-Version': '2022-11-28', 'User-Agent': 'ShortsStudio',
                                   'Content-Type': 'application/json'})
        try:
            with urlopen(request, timeout=30) as response:
                data = response.read()
                return response.status, json.loads(data) if data else None
        except HTTPError as error:
            return error.code, None

    def get(self, name):
        status, data = self.call('GET', '/' + name)
        if status == 404:
            return None
        if status != 200:
            raise RuntimeError(f'Reading {name} failed ({status}). Check the token\'s Variables permission.')
        try:
            return json.loads(data['value'])
        except (ValueError, KeyError, TypeError):
            return None

    def set(self, name, value):
        body = {'name': name, 'value': json.dumps(value)}
        status, _ = self.call('PATCH', '/' + name, body)
        if status == 404:
            status, _ = self.call('POST', '', body)
        if status not in (200, 201, 204):
            raise RuntimeError(f'Writing {name} failed ({status}). Check the token\'s Variables permission.')

    def delete(self, name):
        self.call('DELETE', '/' + name)


def save_secret(repo, token, value, run=subprocess.run):
    # gh encrypts the value with the repository's public key; it is read from stdin, never argv.
    done = run(['gh', 'secret', 'set', SECRET, '--repo', repo], input=value, text=True,
               capture_output=True, env={'PATH': os.environ.get('PATH', ''), 'GH_TOKEN': token}, timeout=60)
    if done.returncode != 0:
        raise RuntimeError('Saving the new token failed. The GitHub token needs the Secrets (read and write) permission.')


class Terminal:
    """`claude setup-token` in a wide pseudo-terminal, so links and tokens are not wrapped."""

    def __init__(self, binary, home):
        self.master, slave = pty.openpty()
        fcntl.ioctl(slave, termios.TIOCSWINSZ, struct.pack('HHHH', 50, 1000, 0, 0))
        env = {'PATH': os.environ.get('PATH', ''), 'HOME': home, 'TERM': 'xterm-256color', 'LANG': 'C.UTF-8',
               'DISABLE_AUTOUPDATER': '1'}
        self.process = subprocess.Popen([binary, 'setup-token'], stdin=slave, stdout=slave, stderr=slave,
                                        env=env, start_new_session=True, close_fds=True)
        os.close(slave)
        self.buffer = b''

    def read(self, seconds):
        end = time.time() + seconds
        while time.time() < end:
            ready, _, _ = select.select([self.master], [], [], 0.5)
            if ready:
                try:
                    chunk = os.read(self.master, 65536)
                except OSError:
                    return clean(self.buffer)
                if not chunk:
                    break
                self.buffer += chunk
            elif self.process.poll() is not None:
                break
        return clean(self.buffer)

    def wait_for(self, find, seconds):
        end = time.time() + seconds
        while time.time() < end:
            found = find(self.read(1))
            if found:
                return found
            if self.process.poll() is not None:
                return find(self.read(1))
        return None

    def type(self, text):
        os.write(self.master, text.encode())
        time.sleep(0.3)
        os.write(self.master, b'\r')

    def close(self):
        if self.process.poll() is None:
            try:
                os.killpg(self.process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            os.close(self.master)
        except OSError:
            pass


def renew(nonce, variables, save, terminal_factory, wait_code=600, poll=3.0):
    def state(value, message='', **extra):
        variables.set(STATE, {'nonce': nonce, 'state': value, 'message': message, 'updatedAt': int(time.time()), **extra})

    terminal = None
    try:
        state('starting', 'Starting the official Claude sign-in…')
        terminal = terminal_factory()
        url = terminal.wait_for(sign_in_url, 90)
        if not url:
            raise RuntimeError('Claude Code did not show a sign-in link. Try again in a minute.')
        state('link', 'Open the sign-in link, approve, then paste the code here.', url=url)
        log('sign-in link published; waiting for the code')
        code, end = None, time.time() + wait_code
        while time.time() < end:
            message = variables.get(CODE)
            if message and message.get('nonce') == nonce:
                variables.delete(CODE)
                code = str(message.get('code', '')).strip()
                break
            terminal.read(poll)
        if not code:
            raise RuntimeError('No code arrived within 10 minutes. Start the renewal again.')
        if not CODE_SHAPE.fullmatch(code):
            raise RuntimeError('That does not look like a Claude code. Copy the whole code and try again.')
        state('checking', 'Checking the code with Claude…')
        mark = len(terminal.read(0))
        terminal.type(code)
        # Wait for the token, or for an error printed after the code was entered.
        output = terminal.wait_for(lambda text: text[mark:] if TOKEN.search(text[mark:]) or
                                   re.search(r'invalid|expired|error|failed', text[mark:], re.I) else None, 90) or ''
        match = TOKEN.search(output)
        if not match:
            raise RuntimeError('Claude did not accept the code. Start again and paste the newest code.')
        token = match.group(0)
        print('::add-mask::' + token, flush=True)
        state('saving', 'Saving your new Claude sign-in…')
        save(token)
        state('done', 'Claude is signed in again. The new sign-in lasts one year.')
        log('token renewed')
        return True
    except Exception as error:
        message = str(error) if isinstance(error, RuntimeError) else 'The renewal stopped unexpectedly. Try again.'
        log('failed: ' + message)
        try:
            state('failed', message)
        except Exception:
            pass
        return False
    finally:
        if terminal:
            terminal.close()
        try:
            variables.delete(CODE)
        except Exception:
            pass


def main():
    nonce = os.environ.get('LOGIN_NONCE', '')
    if not re.fullmatch(r'[0-9a-f]{16,64}', nonce):
        raise SystemExit('Invalid sign-in request')
    repo, token = os.environ['GITHUB_REPOSITORY'], os.environ.get('STUDIO_GITHUB_TOKEN', '')
    if not token:
        raise SystemExit('Add the STUDIO_GITHUB_TOKEN secret first.')
    binary = shutil.which('claude')
    if not binary:
        raise SystemExit('Claude Code is not installed')
    variables = Variables(repo, token)
    with tempfile.TemporaryDirectory(prefix='claude-login-') as home:
        ok = renew(nonce, variables, lambda value: save_secret(repo, token, value), lambda: Terminal(binary, home))
    sys.exit(0 if ok else 1)


if __name__ == '__main__':
    main()
