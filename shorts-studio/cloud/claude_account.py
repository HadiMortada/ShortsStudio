"""Keep an official Claude account login usable for both inference and usage reads.

Only the dedicated, serialized account job owns the refresh credential. Model and
usage jobs receive the short-lived access token. Claude Code performs the actual
OAuth refresh; this module does not implement its token-exchange protocol.
"""
import json
import math
import os
import shutil
import subprocess
import tempfile
import time
from pathlib import Path

ACCOUNT_SECRET = 'CLAUDE_ACCOUNT_OAUTH_JSON'
TOKEN_SECRET = 'CLAUDE_CODE_OAUTH_TOKEN'
ACCOUNT_RECORD = 'STUDIO_CLAUDE_ACCOUNT'
RECONNECT = 'Reconnect Claude from Account in Shorts Studio to enable account usage checks.'

# GitHub snapshots repository secrets when a workflow is queued. A renewed access
# token therefore travels to dependent jobs in an authenticated encrypted envelope,
# using the existing private notification key. The refresh token never leaves the
# account job. The envelope is bound to one repository and workflow run.
ENVELOPE_JS = r'''
const crypto = require('node:crypto'), fs = require('node:fs');
try {
  const x = JSON.parse(fs.readFileSync(0, 'utf8'));
  const key = crypto.createHash('sha256').update(x.key).digest();
  const aad = Buffer.from(x.context);
  if (process.argv[1] === 'seal') {
    const iv = crypto.randomBytes(12), cipher = crypto.createCipheriv('aes-256-gcm', key, iv);
    cipher.setAAD(aad);
    const data = Buffer.concat([cipher.update(x.data, 'utf8'), cipher.final()]);
    process.stdout.write(Buffer.concat([iv, cipher.getAuthTag(), data]).toString('base64url'));
  } else {
    const bytes = Buffer.from(x.data, 'base64url');
    const decipher = crypto.createDecipheriv('aes-256-gcm', key, bytes.subarray(0, 12));
    decipher.setAAD(aad); decipher.setAuthTag(bytes.subarray(12, 28));
    process.stdout.write(Buffer.concat([decipher.update(bytes.subarray(28)), decipher.final()]).toString('utf8'));
  }
} catch { process.exit(1); }
'''


class AccountUnavailable(RuntimeError):
    pass


def envelope(mode, data, source, *, context=None):
    key = source.get('STUDIO_NOTIFY_KEY', '')
    repo, run_id = source.get('GITHUB_REPOSITORY', ''), source.get('GITHUB_RUN_ID', '')
    node = shutil.which('node', path=source.get('PATH'))
    if len(key) < 20 or not repo or not (context or run_id) or not node:
        raise AccountUnavailable('The Claude account handoff is unavailable. Open Studio and reconnect Claude.')
    try:
        result = subprocess.run([node, '-e', ENVELOPE_JS, mode],
            input=json.dumps({'key': key, 'context': repo + ':' + (context or run_id), 'data': data}),
            env={'PATH': source.get('PATH', '')}, capture_output=True, text=True, timeout=15)
        if result.returncode:
            raise ValueError()
        return result.stdout
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise AccountUnavailable('The Claude account handoff could not be verified. Retry the workflow.') from None


def access_token(source):
    encrypted = source.get('STUDIO_CLAUDE_ACCESS', '')
    if not encrypted:
        return source.get(TOKEN_SECRET, '').strip()
    if len(encrypted) > 40000:
        raise AccountUnavailable('The Claude account handoff is invalid.')
    try:
        value = json.loads(envelope('open', encrypted, source))
        token, expires = value['accessToken'], value['expiresAt']
        if not isinstance(token, str) or not 20 <= len(token) <= 16384 or any(ord(c) < 33 or ord(c) > 126 for c in token):
            raise ValueError()
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires) or expires <= time.time() * 1000:
            raise ValueError()
        if source.get('GITHUB_ACTIONS') == 'true':
            # Masking in the preparation job does not carry to a dependent job.
            print('::add-mask::' + token, flush=True)
        return token
    except (ValueError, KeyError, TypeError):
        raise AccountUnavailable('The Claude account handoff expired or is invalid. Retry the full workflow.') from None


def account_credentials(value):
    try:
        value = json.loads(value) if isinstance(value, str) else value
        value = value.get('claudeAiOauth', value) if isinstance(value, dict) else None
        if not isinstance(value, dict):
            raise ValueError()
        result = {}
        for key in ('accessToken', 'refreshToken'):
            token = value.get(key)
            if not isinstance(token, str) or not 20 <= len(token) <= 16384 or any(ord(c) < 33 or ord(c) > 126 for c in token):
                raise ValueError()
            result[key] = token
        expires = value.get('expiresAt')
        if isinstance(expires, bool) or not isinstance(expires, (int, float)) or not math.isfinite(expires) or expires <= 0:
            raise ValueError()
        result['expiresAt'] = expires
        scopes = value.get('scopes')
        if not isinstance(scopes, list) or not all(isinstance(s, str) and 1 <= len(s) <= 100 and not any(c.isspace() for c in s) for s in scopes):
            raise ValueError()
        if not {'user:profile', 'user:inference'}.issubset(scopes):
            raise ValueError()
        result['scopes'] = list(dict.fromkeys(scopes))
        for key in ('subscriptionType', 'rateLimitTier'):
            if isinstance(value.get(key), str) and len(value[key]) <= 80:
                result[key] = value[key]
        return result
    except (ValueError, TypeError, OverflowError):
        raise AccountUnavailable(RECONNECT) from None


def credentials_file(home):
    return Path(home) / '.claude' / '.credentials.json'


def mask(account):
    for key in ('accessToken', 'refreshToken'):
        print('::add-mask::' + account[key], flush=True)


def set_secret(repo, token, name, value, *, run=subprocess.run):
    done = run(['gh', 'secret', 'set', name, '--repo', repo], input=value, text=True,
               capture_output=True, env={'PATH': os.environ.get('PATH', ''), 'GH_TOKEN': token}, timeout=60)
    if done.returncode:
        raise AccountUnavailable('Saving the Claude account failed. The Studio GitHub token needs Secrets read and write permission.')


def latest_account(source, *, run=subprocess.run):
    # Unlike repository secrets, a variable read is evaluated when the job runs,
    # so a queued workflow cannot reuse a refresh token consumed by an earlier job.
    repo, token = source.get('GITHUB_REPOSITORY', ''), source.get('STUDIO_GITHUB_TOKEN', '')
    if repo and token:
        try:
            done = run(['gh', 'api', f'repos/{repo}/actions/variables/{ACCOUNT_RECORD}'],
                env={'PATH': source.get('PATH', ''), 'GH_TOKEN': token}, capture_output=True, text=True, timeout=30)
            if done.returncode == 0:
                encrypted = json.loads(done.stdout)['value']
                if not isinstance(encrypted, str) or len(encrypted) > 45000:
                    raise ValueError()
                return account_credentials(envelope('open', encrypted, source, context='account'))
            if '404' not in (done.stderr or ''):
                raise AccountUnavailable('The current Claude account could not be loaded. Retry the workflow.')
        except (OSError, ValueError, KeyError, TypeError, subprocess.TimeoutExpired):
            raise AccountUnavailable('The current Claude account could not be verified. ' + RECONNECT) from None
    return account_credentials(source.get(ACCOUNT_SECRET, ''))


def save_account(repo, token, value, *, source=None, run=subprocess.run):
    source = os.environ if source is None else source
    source = {**source, 'GITHUB_REPOSITORY': repo}
    account = account_credentials(value)
    mask(account)
    encrypted = envelope('seal', json.dumps({'claudeAiOauth': account}), source, context='account')
    body = json.dumps({'name': ACCOUNT_RECORD, 'value': encrypted})
    for method, path in [('PATCH', f'repos/{repo}/actions/variables/{ACCOUNT_RECORD}'),
                         ('POST', f'repos/{repo}/actions/variables')]:
        done = run(['gh', 'api', '--method', method, path, '--input', '-'], input=body, text=True,
                   capture_output=True, env={'PATH': source.get('PATH', ''), 'GH_TOKEN': token}, timeout=30)
        if done.returncode == 0:
            break
        if method == 'PATCH' and '404' in (done.stderr or ''):
            continue
        raise AccountUnavailable('Saving the Claude account failed. The Studio GitHub token needs Variables read and write permission.')
    # Save the rotated refresh credential first. A failed access-token write can
    # be retried using this account without consuming an old refresh token again.
    set_secret(repo, token, ACCOUNT_SECRET, json.dumps({'claudeAiOauth': account}), run=run)
    set_secret(repo, token, TOKEN_SECRET, account['accessToken'], run=run)


def refresh_account(account, binary, home, *, run=subprocess.run, source=None):
    source = os.environ if source is None else source
    env = {k: source[k] for k in ('PATH', 'LANG', 'LC_ALL', 'TZ') if k in source}
    env.update(HOME=str(home), CLAUDE_CONFIG_DIR=str(Path(home) / '.claude'), NO_COLOR='1',
               CLAUDE_CODE_OAUTH_REFRESH_TOKEN=account['refreshToken'],
               CLAUDE_CODE_OAUTH_SCOPES=' '.join(account['scopes']), DISABLE_AUTOUPDATER='1')
    try:
        # This supported Claude Code login route renews the saved account without
        # a model request. Never pass the GitHub token or another provider's keys.
        done = run([binary, 'auth', 'login', '--claudeai'], env=env, capture_output=True,
                   text=True, timeout=90, stdin=subprocess.DEVNULL, cwd=str(home))
        if done.returncode:
            raise AccountUnavailable(RECONNECT)
        return account_credentials(json.loads(credentials_file(home).read_text(encoding='utf-8')))
    except (OSError, ValueError, subprocess.TimeoutExpired):
        raise AccountUnavailable('Claude account renewal could not complete. ' + RECONNECT) from None


def prepare_account(*, source=None, run=subprocess.run, now=None, binary=None):
    source = os.environ if source is None else source
    account = latest_account(source, run=run)
    repo, token = source.get('GITHUB_REPOSITORY', ''), source.get('STUDIO_GITHUB_TOKEN', '')
    if not repo or not token:
        raise AccountUnavailable('The Studio account job needs the existing STUDIO_GITHUB_TOKEN secret.')
    mask(account)
    now = time.time() if now is None else now
    renewed = account['expiresAt'] <= (now + 600) * 1000
    if renewed:
        binary = binary or shutil.which('claude', path=source.get('PATH'))
        if not binary:
            raise AccountUnavailable('Claude Code is not installed in the account job.')
        with tempfile.TemporaryDirectory(prefix='claude-renew-') as home:
            account = refresh_account(account, binary, home, run=run, source=source)
        if account['expiresAt'] <= (now + 60) * 1000:
            raise AccountUnavailable('Claude returned an expired account login. ' + RECONNECT)
        save_account(repo, token, account, source=source, run=run)
    else:
        # Repairs an interrupted save and keeps the secret backup consistent with
        # the dynamically read account record, without consuming a refresh token.
        save_account(repo, token, account, source=source, run=run)
    return account, renewed


def main():
    try:
        source = os.environ
        account, renewed = prepare_account(source=source)
        encrypted = envelope('seal', json.dumps({k: account[k] for k in ('accessToken', 'expiresAt')}), source)
        if source.get('GITHUB_OUTPUT'):
            with open(source['GITHUB_OUTPUT'], 'a', encoding='utf-8') as output:
                output.write('access_envelope=' + encrypted + '\n')
        print('Claude account renewed.' if renewed else 'Claude account is ready.')
    except AccountUnavailable as error:
        raise SystemExit(str(error)) from None


if __name__ == '__main__':
    main()
