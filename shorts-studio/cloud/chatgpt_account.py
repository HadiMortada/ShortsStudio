"""Keep an official ChatGPT sign-in usable for Codex films, edits, ideas and usage checks.

Mirrors claude_account.py. The account is the Codex CLI's own auth.json from
`codex login --device-auth` (chatgpt_login.py). Only the dedicated, serialized account job holds
the refresh credential and renews it, through the Codex app server's official `account/read`
token refresh; this module does not implement OpenAI's token exchange. Model and usage jobs get
an encrypted, run-bound copy without the refresh credential, so they can never rotate it.
"""
import base64
import json
import math
import os
import select
import shutil
import subprocess
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

from claude_account import AccountUnavailable, envelope as _envelope, mask_value, set_secret, write_variable

ACCOUNT_SECRET = 'CHATGPT_ACCOUNT_JSON'
ACCOUNT_RECORD = 'STUDIO_CHATGPT_ACCOUNT'
ACCESS_RECORD = 'STUDIO_CHATGPT_USAGE_ACCESS'
RECONNECT = 'Reconnect ChatGPT from Account in Shorts Studio.'
# A film job can run 100 minutes; renew well before the access credential could expire during one.
RENEW_BEFORE = 3 * 3600


def envelope(mode, data, source, *, context=None):
    try:
        return _envelope(mode, data, source, context=context)
    except AccountUnavailable as error:
        raise AccountUnavailable(str(error).replace('Claude', 'ChatGPT')) from None


def jwt_claims(token):
    """The payload of a JWT, without verifying it (only used to read expiry and account id)."""
    try:
        part = token.split('.')[1]
        value = json.loads(base64.urlsafe_b64decode(part + '=' * (-len(part) % 4)))
        return value if isinstance(value, dict) else {}
    except (IndexError, ValueError, TypeError, AttributeError):
        return {}


def _token(value, low=20):
    if not isinstance(value, str) or not low <= len(value) <= 16384 or any(ord(c) < 33 or ord(c) > 126 for c in value):
        raise ValueError()
    return value


def account_credentials(value):
    """A validated account from Codex's auth.json (or the saved form of it)."""
    try:
        value = json.loads(value) if isinstance(value, str) else value
        if not isinstance(value, dict):
            raise ValueError()
        tokens = value.get('tokens') if isinstance(value.get('tokens'), dict) else value
        result = {'idToken': _token(tokens.get('id_token', tokens.get('idToken'))),
                  'accessToken': _token(tokens.get('access_token', tokens.get('accessToken'))),
                  'refreshToken': _token(tokens.get('refresh_token', tokens.get('refreshToken')), 10)}
        auth = jwt_claims(result['idToken']).get('https://api.openai.com/auth') or {}
        account_id = tokens.get('account_id', tokens.get('accountId')) or auth.get('chatgpt_account_id')
        if not isinstance(account_id, str) or not 1 <= len(account_id) <= 200 or any(c.isspace() for c in account_id):
            raise ValueError()
        result['accountId'] = account_id
        expires = jwt_claims(result['accessToken']).get('exp')
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires) or expires <= 0:
            raise ValueError()
        result['expiresAt'] = expires * 1000
        plan = auth.get('chatgpt_plan_type')
        if isinstance(plan, str) and len(plan) <= 40:
            result['plan'] = plan
        return result
    except (ValueError, TypeError, OverflowError, AttributeError):
        raise AccountUnavailable(RECONNECT) from None


def auth_file(account, refresh=True):
    """Codex's auth.json for this account. Without refresh, Codex cannot rotate the saved credential."""
    return {'auth_mode': 'chatgpt', 'OPENAI_API_KEY': None,
            'tokens': {'id_token': account['idToken'], 'access_token': account['accessToken'],
                       'refresh_token': account['refreshToken'] if refresh else '', 'account_id': account['accountId']},
            'last_refresh': datetime.now(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S.%fZ')}


def handoff(account):
    """What a model or usage job receives: never the refresh credential."""
    return {k: account[k] for k in ('idToken', 'accessToken', 'accountId', 'expiresAt')}


def access_token(source):
    """The short-lived sign-in handed to this job (dict), or None when ChatGPT is not connected."""
    encrypted = source.get('STUDIO_CHATGPT_ACCESS', '')
    if not encrypted:
        return None
    if len(encrypted) > 60000:
        raise AccountUnavailable('The ChatGPT account handoff is invalid.')
    try:
        value = json.loads(envelope('open', encrypted, source))
        access = {'idToken': _token(value['idToken']), 'accessToken': _token(value['accessToken']),
                  'accountId': str(value['accountId']), 'expiresAt': value['expiresAt'], 'refreshToken': ''}
        expires = access['expiresAt']
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires) or expires <= time.time() * 1000:
            raise ValueError()
        if not 1 <= len(access['accountId']) <= 200:
            raise ValueError()
        if source.get('GITHUB_ACTIONS') == 'true':
            print('::add-mask::' + access['accessToken'], flush=True)
            print('::add-mask::' + access['idToken'], flush=True)
        return access
    except (ValueError, KeyError, TypeError):
        raise AccountUnavailable('The ChatGPT account handoff expired or is invalid. Retry the full workflow.') from None


def mask(account):
    for key in ('idToken', 'accessToken', 'refreshToken'):
        mask_value(account.get(key))


def latest_account(source, *, run=subprocess.run):
    # Read when the job runs (unlike a queued secret), so a used refresh credential is never reused.
    repo, token = source.get('GITHUB_REPOSITORY', ''), source.get('STUDIO_GITHUB_TOKEN', '')
    if repo and token:
        try:
            done = run(['gh', 'api', f'repos/{repo}/actions/variables/{ACCOUNT_RECORD}'],
                env={'PATH': source.get('PATH', ''), 'GH_TOKEN': token}, capture_output=True, text=True, timeout=30)
            if done.returncode == 0:
                encrypted = json.loads(done.stdout)['value']
                if not isinstance(encrypted, str) or len(encrypted) > 60000:
                    raise ValueError()
                return account_credentials(envelope('open', encrypted, source, context='chatgpt-account'))
            if '404' not in (done.stderr or ''):
                raise AccountUnavailable('The current ChatGPT account could not be loaded. Retry the workflow.')
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            raise AccountUnavailable('The current ChatGPT account could not be verified. ' + RECONNECT) from None
    return account_credentials(source.get(ACCOUNT_SECRET, ''))


def save_account(repo, token, value, *, source=None, run=subprocess.run):
    source = os.environ if source is None else source
    source = {**source, 'GITHUB_REPOSITORY': repo}
    account = value if isinstance(value, dict) and 'idToken' in value else account_credentials(value)
    mask(account)
    saved = json.dumps(auth_file(account))
    write_variable(repo, token, ACCOUNT_RECORD, envelope('seal', saved, source, context='chatgpt-account'),
                   source=source, run=run, failure='Saving the ChatGPT account failed. The Studio GitHub token needs '
                   'Variables read and write permission.')
    set_secret(repo, token, ACCOUNT_SECRET, saved, run=run)
    # The private web page reads usage directly with a short-lived copy (no refresh credential).
    try:
        access = envelope('seal', json.dumps(handoff(account)), source, context='chatgpt-usage-access')
        write_variable(repo, token, ACCESS_RECORD, json.dumps({'envelope': access}), source=source, run=run,
                       failure='Usage shortcut unavailable')
    except (AccountUnavailable, OSError, subprocess.TimeoutExpired):
        print('Usage refresh will use the account workflow.', flush=True)
    return account


def refresh_account(account, binary, home, *, popen=subprocess.Popen, source=None, timeout=90):
    """Renews the sign-in with the Codex app server's account/read token refresh (no model request)."""
    source = os.environ if source is None else source
    codex_home = Path(home) / '.codex'
    codex_home.mkdir(parents=True, exist_ok=True, mode=0o700)
    (codex_home / 'auth.json').write_text(json.dumps(auth_file(account)), encoding='utf-8')
    env = {k: source[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TZ') if k in source}
    env.update(HOME=str(home), CODEX_HOME=str(codex_home), NO_COLOR='1')
    requests = [{'jsonrpc': '2.0', 'id': 0, 'method': 'initialize',
                 'params': {'clientInfo': {'name': 'shorts_studio', 'title': 'Shorts Studio', 'version': '1.0'}}},
                {'jsonrpc': '2.0', 'method': 'initialized'},
                {'jsonrpc': '2.0', 'id': 1, 'method': 'account/read', 'params': {'refreshToken': True}}]
    process = popen([binary, 'app-server'], stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                    env=env, cwd=str(home), text=True, start_new_session=True)
    answer = None
    try:
        process.stdin.write(''.join(json.dumps(r) + '\n' for r in requests))
        process.stdin.flush()
        end = time.monotonic() + timeout
        while time.monotonic() < end and answer is None:
            ready, _, _ = select.select([process.stdout], [], [], 1)
            if not ready:
                if process.poll() is not None:
                    break
                continue
            line = process.stdout.readline()
            if not line:
                break
            try:
                message = json.loads(line)
            except ValueError:
                continue
            if isinstance(message, dict) and message.get('id') == 1:
                answer = message
    except (OSError, ValueError):
        answer = None
    finally:
        if process.poll() is None:
            process.kill()
        process.wait()
    if not answer or 'result' not in answer:
        raise AccountUnavailable('ChatGPT account renewal could not complete. ' + RECONNECT)
    try:
        return account_credentials(json.loads((codex_home / 'auth.json').read_text(encoding='utf-8')))
    except (OSError, ValueError):
        raise AccountUnavailable('ChatGPT account renewal could not complete. ' + RECONNECT) from None


def prepare_account(*, source=None, run=subprocess.run, popen=subprocess.Popen, now=None, binary=None):
    source = os.environ if source is None else source
    account = latest_account(source, run=run)
    repo, token = source.get('GITHUB_REPOSITORY', ''), source.get('STUDIO_GITHUB_TOKEN', '')
    if not repo or not token:
        raise AccountUnavailable('The Studio account job needs the existing STUDIO_GITHUB_TOKEN secret.')
    mask(account)
    now = time.time() if now is None else now
    renewed = account['expiresAt'] <= (now + RENEW_BEFORE) * 1000
    if renewed:
        binary = binary or shutil.which('codex', path=source.get('PATH'))
        if not binary:
            raise AccountUnavailable('The Codex CLI is not installed in the account job.')
        with tempfile.TemporaryDirectory(prefix='codex-renew-') as home:
            account = refresh_account(account, binary, home, popen=popen, source=source)
        if account['expiresAt'] <= (now + 600) * 1000:
            raise AccountUnavailable('ChatGPT returned an expired sign-in. ' + RECONNECT)
    save_account(repo, token, account, source=source, run=run)
    return account, renewed


def main():
    try:
        source = os.environ
        account, renewed = prepare_account(source=source)
        encrypted = envelope('seal', json.dumps(handoff(account)), source)
        if source.get('GITHUB_OUTPUT'):
            with open(source['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
                output.write('access_envelope=' + encrypted + '\n')
        print('ChatGPT account renewed.' if renewed else 'ChatGPT account is ready.')
    except AccountUnavailable as error:
        raise SystemExit(str(error)) from None


if __name__ == '__main__':
    main()
