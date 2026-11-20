"""ChatGPT as the studio's AI: choosing it, running the Codex CLI safely, its sign-in and its usage."""
import asyncio
import base64
import io
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

import actions_film as af
import ai
import ai_usage
import chatgpt_account as ca
import chatgpt_login as cl
import chatgpt_usage as cu
from claude_usage import UsageBlocked


def jwt(payload):
    part = lambda value: base64.urlsafe_b64encode(json.dumps(value).encode()).decode().rstrip('=')
    return part({'alg': 'RS256'}) + '.' + part(payload) + '.' + 's' * 40


def auth_json(expires=None, refresh='rt_' + 'r' * 40):
    expires = expires if expires is not None else time.time() + 10 * 86400
    claims = {'https://api.openai.com/auth': {'chatgpt_account_id': 'acct-1', 'chatgpt_plan_type': 'plus'}}
    return {'auth_mode': 'chatgpt', 'OPENAI_API_KEY': None,
            'tokens': {'id_token': jwt({**claims, 'email': 'private@example.com', 'exp': int(time.time()) + 3600}),
                       'access_token': jwt({**claims, 'exp': int(expires)}), 'refresh_token': refresh,
                       'account_id': 'acct-1'}, 'last_refresh': '2026-11-20T00:00:00Z'}


def source(saved=None, **extra):
    return {'PATH': os.environ.get('PATH', ''), 'STUDIO_NOTIFY_KEY': 'private-notification-key-' + 'x' * 30,
            'GITHUB_REPOSITORY': 'owner/studio', 'GITHUB_RUN_ID': '123', 'STUDIO_GITHUB_TOKEN': 'private-github-token',
            ca.ACCOUNT_SECRET: json.dumps(saved or auth_json()), **extra}


class ChoiceTests(unittest.TestCase):
    def test_setting_chooses_the_ai_and_claude_stays_the_default(self):
        self.assertEqual(ai.provider({}, {}), 'claude')
        self.assertEqual(ai.provider({}, {'ai': 'chatgpt'}), 'chatgpt')
        self.assertEqual(ai.provider({}, {'ai': 'gemini'}), 'claude')
        # The workflow's choice (read when its account job ran) wins over the file.
        self.assertEqual(ai.provider({'STUDIO_AI': 'claude'}, {'ai': 'chatgpt'}), 'claude')
        self.assertEqual(ai.provider({'STUDIO_AI': 'chatgpt'}, {}), 'chatgpt')
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'settings.json'
            path.write_text(json.dumps({'ai': 'chatgpt'}))
            self.assertEqual(ai.engine({'STUDIO_SETTINGS': str(path)}).name, 'ChatGPT')
        self.assertEqual(ai.engine_for('/usr/bin/codex').key, 'chatgpt')
        self.assertEqual(ai.engine_for('/usr/bin/claude').key, 'claude')


class ModelTests(unittest.TestCase):
    def test_each_ai_runs_the_model_chosen_in_settings(self):
        with tempfile.TemporaryDirectory() as root:
            path = Path(root) / 'settings.json'
            path.write_text(json.dumps({'claudeModel': 'claude-sonnet-5-5', 'chatgptModel': 'gpt-6-luna'}))
            with mock.patch.dict(os.environ, {'STUDIO_SETTINGS': str(path)}):
                claude, chatgpt = ai.ENGINES['claude'].args('/bin/claude'), ai.ENGINES['chatgpt'].args('/bin/codex')
                self.assertEqual(claude[claude.index('--model') + 1], 'claude-sonnet-5-5')
                self.assertEqual(chatgpt[chatgpt.index('--model') + 1], 'gpt-6-luna')
                self.assertEqual(ai.ENGINES['chatgpt'].model_label, 'GPT-6 Luna')
                question, _ = ai.ENGINES['chatgpt'].question('/bin/codex', 'hi', images=[b'png'], workdir=root)
                self.assertEqual(question[question.index('--model') + 1], 'gpt-6-luna')
            path.write_text(json.dumps({'claudeModel': 'not-a-model'}))
            with mock.patch.dict(os.environ, {'STUDIO_SETTINGS': str(path)}):
                self.assertEqual(ai.ENGINES['claude'].model, 'claude-opus-5-5', 'unknown models fall back to the default')


class CodexRunTests(unittest.TestCase):
    def test_codex_only_writes_text_with_optional_live_search(self):
        args = ai.codex_args('/bin/codex', 'auto', 'high')
        self.assertEqual(args[:2], ['/bin/codex', 'exec'])
        self.assertIn('--json', args)
        self.assertEqual(args[args.index('--sandbox') + 1], 'read-only')
        self.assertEqual(args[args.index('--model') + 1], 'gpt-6.2-astra')
        self.assertIn('web_search="live"', args)
        self.assertIn('approval_policy="never"', args)
        self.assertIn('model_reasoning_effort="high"', args)
        for tool in ('shell_tool', 'unified_exec', 'apps', 'plugins', 'browser_use', 'computer_use', 'image_generation'):
            self.assertEqual(args[args.index(tool) - 1], '--disable', tool)
        self.assertNotIn('--dangerously-bypass-approvals-and-sandbox', args)
        self.assertIn('web_search="disabled"', ai.codex_args('/bin/codex', 'off'))
        self.assertIn('--image=/tmp/a.png', ai.codex_args('/bin/codex', 'off', images=['/tmp/a.png']))

    def test_only_the_final_answer_counts_and_search_is_audited(self):
        lines = [{'type': 'thread.started'}, {'type': 'error', 'message': 'Reconnecting... 2/5'},
                 {'type': 'item.completed', 'item': {'type': 'web_search', 'query': 'tides'}},
                 {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': 'thinking aloud'}},
                 {'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{"title":"A film"}'}},
                 {'type': 'turn.completed', 'usage': {}}]
        result, audit = ai.unpack_codex('\n'.join(map(json.dumps, lines)))
        self.assertEqual(json.loads(result)['title'], 'A film')
        self.assertTrue(audit['searchUsed'])
        with self.assertRaisesRegex(RuntimeError, 'usage limit'):
            ai.unpack_codex(json.dumps({'type': 'turn.failed', 'error': {'message': "You've hit your usage limit."}}))
        with self.assertRaises(RuntimeError):
            ai.unpack_codex(json.dumps({'type': 'turn.started'}))

    def test_author_runs_codex_and_reports_failures_by_name(self):
        answer = json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{"ok":1}'}})
        calls = []

        async def run(args, **kw):
            calls.append((args, kw))
            return 0, answer, ''
        author = af.make_author('/bin/codex', {'HOME': '/h'}, 'off', run=run)
        self.assertEqual(asyncio.run(author('brief')), '{"ok":1}')
        self.assertEqual((author.key, author.ai_name, author.model_label), ('chatgpt', 'ChatGPT', 'GPT-6.2 Astra'))
        self.assertIn('exec', calls[0][0])
        self.assertIn('web_search="disabled"', calls[0][0])
        self.assertEqual(calls[0][1]['stdin'], 'brief')

        async def expired(args, **kw):
            return 1, json.dumps({'type': 'turn.failed', 'error': {'message': '401 Unauthorized: token expired'}}), ''
        author = af.make_author('/bin/codex', {}, 'auto', run=expired)
        with self.assertRaisesRegex(RuntimeError, 'ChatGPT sign-in expired'):
            asyncio.run(author('brief'))
        self.assertEqual(author.failure, 'signin')

    def test_visual_review_attaches_the_contact_sheet(self):
        with tempfile.TemporaryDirectory() as root:
            sheet = Path(root) / 'review.png'
            sheet.write_bytes(b'png-bytes')

            async def run(args, **kw):
                image = next(a for a in args if a.startswith('--image='))[len('--image='):]
                self.assertEqual(Path(image).read_bytes(), b'png-bytes')
                self.assertIn('six chronological sampled frames', kw['stdin'])
                text = json.dumps({'type': 'item.completed', 'item': {'type': 'agent_message', 'text': '{"passed":true,"issues":[]}'}})
                return 0, text, ''
            review = af.make_review('/bin/codex', {'HOME': root}, run=run)
            self.assertTrue(asyncio.run(review({'source': 'export const Video = () => null;'}, sheet))['passed'])


class AccountTests(unittest.TestCase):
    def test_sign_in_is_read_from_codex_auth_and_handoff_has_no_refresh_credential(self):
        account = ca.account_credentials(auth_json())
        self.assertEqual((account['accountId'], account['plan']), ('acct-1', 'plus'))
        self.assertNotIn('email', account)
        self.assertNotIn('refreshToken', ca.handoff(account))
        self.assertEqual(ca.auth_file(account, refresh=False)['tokens']['refresh_token'], '')
        for broken in [{**auth_json(), 'tokens': {**auth_json()['tokens'], 'access_token': 'x'}},
                       {**auth_json(), 'tokens': {**auth_json()['tokens'], 'refresh_token': ''}}, {}]:
            with self.assertRaises(ca.AccountUnavailable):
                ca.account_credentials(broken)

    def test_codex_gets_only_its_own_short_lived_sign_in(self):
        s = source()
        account = ca.account_credentials(auth_json())
        s['STUDIO_CHATGPT_ACCESS'] = ca.envelope('seal', json.dumps(ca.handoff(account)), s)
        with tempfile.TemporaryDirectory() as home:
            env = ai.ChatGPT().env(home, s)
            self.assertEqual(env['HOME'], home)
            self.assertFalse({'STUDIO_NOTIFY_KEY', 'STUDIO_GITHUB_TOKEN', ca.ACCOUNT_SECRET, 'STUDIO_CHATGPT_ACCESS'} & set(env))
            saved = json.loads((Path(env['CODEX_HOME']) / 'auth.json').read_text())
            self.assertEqual(saved['tokens']['access_token'], account['accessToken'])
            self.assertEqual(saved['tokens']['refresh_token'], '')
            self.assertEqual(oct((Path(env['CODEX_HOME']) / 'auth.json').stat().st_mode & 0o777), '0o600')
        with self.assertRaises(ca.AccountUnavailable):
            ca.access_token({**s, 'GITHUB_RUN_ID': '124'})
        with self.assertRaisesRegex(RuntimeError, 'Reconnect ChatGPT'):
            ai.ChatGPT().env('/tmp/h', {'PATH': '/bin'})

    def test_renewal_uses_the_codex_app_server_only_when_needed(self):
        saved = []

        def run(args, **kwargs):
            if args[1] == 'api' and '--method' not in args:
                return SimpleNamespace(returncode=1, stdout='', stderr='HTTP 404')
            saved.append(args)
            return SimpleNamespace(returncode=0, stdout='{}', stderr='')

        def no_popen(*a, **k):
            raise AssertionError('a fresh sign-in is not renewed')
        account, renewed = ca.prepare_account(source=source(), run=run, popen=no_popen)
        self.assertFalse(renewed)
        self.assertTrue(any(ca.ACCOUNT_SECRET in a for a in saved))

        fresh = auth_json(expires=time.time() + 10 * 86400, refresh='rt_' + 'n' * 40)

        class AppServer:
            def __init__(self, args, **kwargs):
                assert args[1:] == ['app-server']
                self.home = Path(kwargs['env']['CODEX_HOME'])
                self.stdin, self.returncode = io.StringIO(), None
                read, write = os.pipe()
                os.write(write, (json.dumps({'id': 0, 'result': {}}) + '\n' + json.dumps({'id': 1, 'result': {'account': {}}}) + '\n').encode())
                os.close(write)
                self.stdout = os.fdopen(read)
                (self.home / 'auth.json').write_text(json.dumps(fresh))

            def poll(self):
                return self.returncode

            def kill(self):
                self.returncode = -9

            def wait(self):
                self.stdout.close()
        account, renewed = ca.prepare_account(source=source(auth_json(expires=time.time() + 600)), run=run,
                                              popen=AppServer, binary='/bin/codex')
        self.assertTrue(renewed)
        self.assertEqual(account['refreshToken'], 'rt_' + 'n' * 40)


class UsageTests(unittest.TestCase):
    def test_plus_and_pro_windows_become_session_and_weekly(self):
        data = {'rate_limit': {'primary_window': {'used_percent': 33, 'limit_window_seconds': 18000, 'reset_at': 1795300000},
                               'secondary_window': {'used_percent': 12.5, 'limit_window_seconds': 604800, 'reset_at': 1795700000}}}
        usage = cu.validate_usage(data)
        self.assertEqual((usage['sessionPercent'], usage['weeklyPercent']), (33, 12.5))
        self.assertTrue(usage['sessionResetsAt'].endswith('Z'))
        weekly_only = cu.validate_usage({'rate_limit': {'primary_window': {'used_percent': 42, 'limit_window_seconds': 604800},
                                                        'secondary_window': None}})
        self.assertEqual((weekly_only['sessionPercent'], weekly_only['weeklyPercent']), (0, 42))
        for bad in [{}, {'rate_limit': {'primary_window': {'used_percent': 'x'}}}, {'rate_limit': {'primary_window': {'used_percent': 140}}}]:
            with self.assertRaises(UsageBlocked):
                cu.validate_usage(bad)

    def test_read_usage_sends_the_account_and_limits_block_with_chatgpt_name(self):
        s = source(STUDIO_AI='chatgpt')
        account = ca.account_credentials(auth_json())
        s['STUDIO_CHATGPT_ACCESS'] = ca.envelope('seal', json.dumps(ca.handoff(account)), s)

        def open_url(request, timeout):
            self.assertEqual(request.full_url, cu.USAGE_URL)
            self.assertEqual(request.get_header('Authorization'), 'Bearer ' + account['accessToken'])
            self.assertEqual(request.get_header('Chatgpt-account-id'), 'acct-1')
            return io.BytesIO(json.dumps({'rate_limit': {'primary_window': {'used_percent': 91, 'limit_window_seconds': 18000},
                                                          'secondary_window': {'used_percent': 20, 'limit_window_seconds': 604800}}}).encode())
        self.assertEqual(ai_usage.read_usage(source=s, open_url=open_url)['sessionPercent'], 91)
        with self.assertRaisesRegex(UsageBlocked, 'ChatGPT session usage is 91%'):
            ai_usage.require_usage(source=s, open_url=open_url, limits={'sessionUsageLimit': 70, 'weeklyUsageLimit': 70})
        with self.assertRaisesRegex(UsageBlocked, 'sign in to ChatGPT'):
            cu.read_usage(source={'HOME': '/nonexistent', 'STUDIO_AI': 'chatgpt'})


class LoginTests(unittest.TestCase):
    OUTPUT = ('Welcome to Codex\nFollow these steps to sign in with ChatGPT using device code ABCD-EFGHI:\n\n'
              '1. Open this link in your browser and sign in to your account\nhttps://auth.openai.com/codex/device\n\n'
              '2. Enter this one-time code (expires in 15 minutes)\nABCD-EFGHI\n')

    def test_only_openai_links_and_device_codes_are_published(self):
        self.assertEqual(cl.sign_in_link(self.OUTPUT), ('https://auth.openai.com/codex/device', 'ABCD-EFGHI'))
        self.assertIsNone(cl.sign_in_link('Open https://evil.example/codex/device and enter ABCD-EFGHI'))
        self.assertIsNone(cl.sign_in_link('https://auth.openai.com/codex/device'))

    def test_device_sign_in_is_saved_without_reaching_the_phone(self):
        states, saved = [], []
        variables = SimpleNamespace(set=lambda name, value: states.append(value))

        class Login:
            def __init__(self, ok=True):
                self.ok, self.reads = ok, 0

            def wait_for(self, find, seconds):
                return find(LoginTests.OUTPUT)

            def read(self, seconds):
                return LoginTests.OUTPUT

            def finished(self, seconds):
                self.reads += 1
                return None if self.reads < 2 else self.ok

            def credentials(self):
                return ca.account_credentials(auth_json())

            def close(self):
                pass
        self.assertTrue(cl.connect('a' * 32, variables, saved.append, Login, poll=0))
        link = next(s for s in states if s['state'] == 'link')
        self.assertEqual((link['url'], link['userCode'], link['provider']), ('https://auth.openai.com/codex/device', 'ABCD-EFGHI', 'chatgpt'))
        self.assertEqual(states[-1]['state'], 'done')
        tokens = auth_json()['tokens']
        for secret in (tokens['access_token'].split('.')[1], tokens['refresh_token']):
            self.assertFalse(any(secret in json.dumps(s) for s in states), 'the phone never receives a credential')
        self.assertEqual(saved[0]['accountId'], 'acct-1')
        states.clear()
        self.assertFalse(cl.connect('b' * 32, variables, saved.append, lambda: Login(ok=False), poll=0))
        self.assertEqual(states[-1]['state'], 'failed')


if __name__ == '__main__':
    unittest.main()
