import copy
import io
import json
import os
import tempfile
import time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace

from claude_account import (ACCOUNT_RECORD, ACCOUNT_SECRET, TOKEN_SECRET, AccountUnavailable,
    access_token, account_credentials, credentials_file, envelope, prepare_account, save_account)
from actions_film import claude_env
from claude_usage import read_usage

ACCESS = 'sk-ant-oat01-' + 'a' * 40
REFRESH = 'sk-ant-ort01-' + 'r' * 40


def account(expires=None):
    return {'accessToken': ACCESS, 'refreshToken': REFRESH,
            'expiresAt': expires if expires is not None else (time.time() + 3600) * 1000,
            'scopes': ['user:profile', 'user:inference', 'user:sessions:claude_code']}


def source(saved=None):
    return {'PATH': os.environ.get('PATH', ''), 'STUDIO_NOTIFY_KEY': 'private-notification-key-' + 'x' * 30,
            'GITHUB_REPOSITORY': 'owner/studio', 'GITHUB_RUN_ID': '123', 'STUDIO_GITHUB_TOKEN': 'private-github-token',
            ACCOUNT_SECRET: json.dumps({'claudeAiOauth': saved or account()})}


class AccountTests(unittest.TestCase):
    def test_full_profile_required_and_unneeded_fields_dropped(self):
        value = account()
        value['email'] = 'private@example.com'
        self.assertNotIn('email', account_credentials(value))
        for change in [{'scopes': ['user:inference']}, {'refreshToken': ''}, {'accessToken': ''},
                       {'expiresAt': True}, {'expiresAt': float('nan')}]:
            with self.subTest(change=change), self.assertRaises(AccountUnavailable):
                account_credentials({**value, **change})

    def test_handoff_is_encrypted_authenticated_and_bound_to_a_workflow(self):
        s = source()
        plain = json.dumps({k: account()[k] for k in ('accessToken', 'expiresAt')})
        encrypted = envelope('seal', plain, s)
        self.assertNotIn(ACCESS, encrypted)
        self.assertNotEqual(encrypted, envelope('seal', plain, s), 'random nonce per handoff')
        self.assertEqual(envelope('open', encrypted, s), plain)
        self.assertEqual(access_token({**s, 'STUDIO_CLAUDE_ACCESS': encrypted}), ACCESS)
        for altered in [{**s, 'GITHUB_RUN_ID': '124'}, {**s, 'GITHUB_REPOSITORY': 'other/repo'},
                        {**s, 'STUDIO_NOTIFY_KEY': 'different-private-key-' + 'y' * 30}]:
            with self.assertRaises(AccountUnavailable):
                access_token({**altered, 'STUDIO_CLAUDE_ACCESS': encrypted})
        with self.assertRaises(AccountUnavailable):
            access_token({**s, 'STUDIO_CLAUDE_ACCESS': ('a' if encrypted[0] != 'a' else 'b') + encrypted[1:]})

    def test_model_and_usage_get_the_renewed_access_not_a_queued_old_secret(self):
        s = source()
        encrypted = envelope('seal', json.dumps({'accessToken': ACCESS, 'expiresAt': (time.time() + 600) * 1000}), s)
        s.update(STUDIO_CLAUDE_ACCESS=encrypted, CLAUDE_CODE_OAUTH_TOKEN='old-inference-token')
        env = claude_env('/temporary/claude-home', s)
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN'], ACCESS)
        self.assertFalse({'STUDIO_NOTIFY_KEY', 'STUDIO_GITHUB_TOKEN', ACCOUNT_SECRET, 'STUDIO_CLAUDE_ACCESS'} & set(env))
        def open_url(request, timeout):
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + ACCESS)
            return io.BytesIO(b'{"five_hour":{"utilization":30},"seven_day":{"utilization":40}}')
        self.assertEqual(read_usage(source=s, open_url=open_url)['sessionPercent'], 30)

    def test_expired_handoff_stops_instead_of_falling_back_to_old_token(self):
        s = source()
        encrypted = envelope('seal', json.dumps({'accessToken': ACCESS, 'expiresAt': 1000}), s)
        with self.assertRaises(AccountUnavailable):
            access_token({**s, TOKEN_SECRET: 'old-token', 'STUDIO_CLAUDE_ACCESS': encrypted})

    def test_fresh_account_does_not_consume_refresh_credential(self):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(returncode=1 if args[1] == 'api' and '--method' not in args else 0, stdout='', stderr='404')
        with redirect_stdout(io.StringIO()):
            ready, renewed = prepare_account(source=source(), run=run)
        self.assertFalse(renewed)
        self.assertEqual(ready['accessToken'], ACCESS)
        self.assertEqual(len(calls), 4, 'one latest-record read and three credential persistence writes')
        self.assertFalse(any('login' in args for args, _ in calls))

    def test_latest_encrypted_record_wins_over_a_queued_stale_secret(self):
        s = source(account(1000))
        latest = account()
        encrypted = envelope('seal', json.dumps({'claudeAiOauth': latest}), s, context='account')
        calls = []
        def run(args, **kwargs):
            calls.append(args)
            return SimpleNamespace(returncode=0, stdout=json.dumps({'value': encrypted}), stderr='')
        with redirect_stdout(io.StringIO()):
            ready, renewed = prepare_account(source=s, run=run)
        self.assertFalse(renewed)
        self.assertEqual(ready, latest)
        self.assertFalse(any('login' in args for args in calls))

    def test_official_cli_renews_and_credentials_persist_before_token_sync(self):
        calls = []
        def run(args, **kwargs):
            calls.append((args, kwargs))
            if 'auth' in args:
                self.assertEqual(args[1:], ['auth', 'login', '--claudeai'])
                self.assertEqual(kwargs['env']['CLAUDE_CODE_OAUTH_REFRESH_TOKEN'], REFRESH)
                self.assertIn('user:profile', kwargs['env']['CLAUDE_CODE_OAUTH_SCOPES'])
                self.assertNotIn('STUDIO_GITHUB_TOKEN', kwargs['env'])
                self.assertNotIn(ACCOUNT_SECRET, kwargs['env'])
                file = credentials_file(kwargs['env']['HOME'])
                file.parent.mkdir(parents=True)
                file.write_text(json.dumps({'claudeAiOauth': account()}))
                return SimpleNamespace(returncode=0, stdout='Login successful.', stderr='')
            if args[1] == 'api' and '--method' not in args:
                return SimpleNamespace(returncode=1, stdout='', stderr='404')
            return SimpleNamespace(returncode=0, stdout='', stderr='')
        with redirect_stdout(io.StringIO()):
            ready, renewed = prepare_account(source=source(account(1000)), run=run, binary='/bin/claude')
        self.assertTrue(renewed)
        writes = [(args, kw) for args, kw in calls if '--method' in args or 'secret' in args]
        self.assertIn(ACCOUNT_RECORD, writes[0][0][4])
        self.assertIn(ACCOUNT_SECRET, writes[1][0])
        self.assertIn(TOKEN_SECRET, writes[2][0])
        self.assertFalse(any(REFRESH in ' '.join(args) or ACCESS in ' '.join(args) for args, _ in calls))

    def test_refresh_failure_exposes_no_cli_output_and_does_not_overwrite_account(self):
        def run(args, **kwargs):
            if 'auth' in args:
                return SimpleNamespace(returncode=1, stdout=ACCESS, stderr=REFRESH)
            return SimpleNamespace(returncode=1, stdout='', stderr='404')
        with redirect_stdout(io.StringIO()), self.assertRaises(AccountUnavailable) as raised:
            prepare_account(source=source(account(1000)), run=run, binary='/bin/claude')
        self.assertNotIn(ACCESS, str(raised.exception))
        self.assertNotIn(REFRESH, str(raised.exception))
