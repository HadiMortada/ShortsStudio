import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError

import actions_film as af
from claude_usage import NoRedirect, UsageBlocked, require_usage, validate_usage
from production import ProductionManager


def payload(session=50, weekly=79.99):
    return {'five_hour': {'utilization': session}, 'seven_day': {'utilization': weekly}}


class UsageTests(unittest.TestCase):
    def test_exact_thresholds(self):
        for session, weekly in [(0, 0), (50, 79.99), (49.99, 79)]:
            self.assertEqual(validate_usage(payload(session, weekly))['sessionPercent'], session)
        for session, weekly in [(50.01, 20), (20, 80), (20, 80.01), (100, 100)]:
            with self.subTest(session=session, weekly=weekly), self.assertRaises(UsageBlocked):
                validate_usage(payload(session, weekly))

    def test_unknown_usage_never_means_zero(self):
        for value in [None, True, '20', -1, 101, float('nan'), float('inf'), {}, []]:
            for data in [payload(value, 1), payload(1, value)]:
                with self.subTest(data=data), self.assertRaises(UsageBlocked):
                    validate_usage(data)
        for data in [{}, [], None, {'five_hour': None, 'seven_day': {}}]:
            with self.assertRaises(UsageBlocked):
                validate_usage(data)

    def test_request_is_fresh_read_only_and_uses_selected_credential(self):
        calls = []

        def open_url(request, timeout):
            calls.append(request)
            self.assertEqual(timeout, 20)
            self.assertEqual(request.get_method(), 'GET')
            self.assertEqual(request.full_url, 'https://api.anthropic.com/api/oauth/usage')
            self.assertEqual(request.get_header('Authorization'), 'Bearer private-token')
            return io.BytesIO(json.dumps(payload()).encode())

        for _ in range(2):
            result = require_usage(source={'CLAUDE_CODE_OAUTH_TOKEN': 'private-token'}, open_url=open_url)
            self.assertNotIn('private-token', json.dumps(result))
        self.assertEqual(len(calls), 2, 'no cached usage authorizes a later film')
        with tempfile.TemporaryDirectory() as home:
            config = Path(home) / '.claude'
            config.mkdir()
            (config / '.credentials.json').write_text(json.dumps({'claudeAiOauth': {'accessToken': 'private-token'}}))
            require_usage(source={'HOME': home}, open_url=open_url)

    def test_failures_are_safe_and_stop_creation(self):
        secret = 'private-secret'
        for failure in [HTTPError('https://api.anthropic.com', code, secret, {}, None) for code in (401, 403, 429, 500)]:
            with self.subTest(code=failure.code), self.assertRaises(UsageBlocked) as raised:
                require_usage(source={'CLAUDE_CODE_OAUTH_TOKEN': secret}, open_url=Mock(side_effect=failure))
            self.assertNotIn(secret, str(raised.exception))
        for failure in [TimeoutError(secret), URLError(secret), ValueError(secret)]:
            with self.assertRaises(UsageBlocked) as raised:
                require_usage(source={'CLAUDE_CODE_OAUTH_TOKEN': secret}, open_url=Mock(side_effect=failure))
            self.assertNotIn(secret, str(raised.exception))
        for body in [b'not json', b'x' * 65537]:
            with self.assertRaises(UsageBlocked):
                require_usage(source={'CLAUDE_CODE_OAUTH_TOKEN': secret}, open_url=lambda *a, **kw: io.BytesIO(body))
        with tempfile.TemporaryDirectory() as home, self.assertRaises(UsageBlocked):
            require_usage(source={'HOME': home})
        self.assertIsNone(NoRedirect().redirect_request(None, None, 302, '', {}, 'https://other.example'))


class CreationGateTests(unittest.TestCase):
    def test_manual_autopilot_backup_and_saved_retry_cannot_bypass(self):
        for mode in ('manual', 'autopilot', 'backup', 'saved'):
            with self.subTest(mode=mode), tempfile.TemporaryDirectory() as root:
                settings = Path(root) / 'settings.json'
                settings.write_text(json.dumps({'enabled': True, 'domain': 'Science', 'language': 'English',
                                                 'voice': 'auto', 'times': ['09:00'], 'writer': 'auto'}))
                flags = ['--brief', 'Explain light'] if mode in ('manual', 'backup') else ['--autopilot', str(settings)]
                if mode == 'backup':
                    flags += ['--writer', 'backup']
                if mode == 'saved':
                    from test_actions import DESIGN
                    previous = Path(root) / 'previous'
                    previous.mkdir()
                    (previous / 'design.json').write_text(json.dumps(DESIGN))
                    flags += ['--slot', '2026-11-14T09:00+02:00', '--previous', str(previous)]
                args = af.parse_args(['--renderer', root, '--out', root + '/out', '--history', root + '/history.json',
                                      '--ideas', root + '/ideas.json', *flags])
                with patch('actions_film.require_usage', side_effect=UsageBlocked('Video creation paused: usage unknown.')), \
                     patch('actions_film.produce') as produce, patch('backup.choose_topic') as topic:
                    result = asyncio.run(af.create(args, author=Mock(), review=Mock(), render=Mock(), writers=[]))
                self.assertEqual(result['state'], 'failed', result)
                self.assertEqual(result['claudeIssue'], 'usage')
                self.assertFalse(result['publish'])
                produce.assert_not_called()
                topic.assert_not_called()
                self.assertFalse(Path(root, 'history.json').exists())
                self.assertFalse(Path(root, 'out', 'video.mp4').exists())
                self.assertEqual(json.loads(Path(root, 'out', 'delivery.json').read_text()), result)

    def test_cloud_preflight_precedes_research_and_render(self):
        async def scenario(root):
            author, render = Mock(), Mock()
            def block():
                raise UsageBlocked('Video creation paused: weekly usage is 80%.')
            manager = ProductionManager(root, author, None, preflight=block, render=render)
            state = manager.start('Science', 30, 'auto', options={'autonomous': {'domain': 'Science'}})
            await manager.task
            self.assertEqual(manager.status(state['id'])['state'], 'failed')
            self.assertIn('80%', manager.status(state['id'])['message'])
            author.assert_not_called()
            render.assert_not_called()
        with tempfile.TemporaryDirectory() as root:
            asyncio.run(scenario(root))
