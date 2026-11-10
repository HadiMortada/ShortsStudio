import asyncio
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

os.environ['STUDIO_ACCESS'] = 'test-access-key'
from modal_bridge import create_service, build_cloud
from fastapi.testclient import TestClient


class BridgeTests(unittest.TestCase):
    def test_serialized_image_matches_playground_python(self):
        import modal
        for version in [(3,11,0),(3,12,0)]:
            with patch('sys.version_info',version), patch.object(modal.Image, 'debian_slim',side_effect=RuntimeError('image intercepted')) as image:
                with self.assertRaisesRegex(RuntimeError,'image intercepted'):
                    build_cloud('offline-test-key')
                image.assert_called_once_with(python_version=f'{version[0]}.{version[1]}')

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.calls = []
        async def run(args, **kwargs):
            self.calls.append((args, kwargs))
            if 'status' in args:
                return 0, '{"loggedIn":true}', ''
            return 0, '{"narration":"Hello","scenes":["Hello","World"]}', ''
        self.client = TestClient(create_service(Path(self.tmp.name), run=run))
        self.headers = {'Authorization': 'Bearer test-access-key'}

    def tearDown(self):
        self.tmp.cleanup()

    def test_unauthorized_does_not_start_cli(self):
        self.assertEqual(self.client.get('/status').status_code, 401)
        self.assertEqual(self.client.post('/generate', json={'topic':'Moon'}).status_code, 401)
        self.assertEqual(self.calls, [])

    def test_status_uses_official_cli(self):
        self.assertTrue(self.client.get('/status', headers=self.headers).json()['loggedIn'])
        self.assertEqual(self.calls[0][0][-2:], ['auth', 'status'])

    def test_generate_keeps_normal_claude_permissions_and_passes_topic_via_stdin(self):
        with patch.dict(os.environ, {'ANTHROPIC_API_KEY':'must-not-pass', 'CLAUDE_CODE_OAUTH_TOKEN':'must-not-pass'}):
            response = self.client.post('/generate', headers=self.headers, json={'topic':'$(touch /tmp/no) moon'})
        self.assertEqual(response.status_code, 200)
        args, kwargs = self.calls[-1]
        self.assertNotIn('--tools', args)
        self.assertNotIn('$(touch /tmp/no) moon', args)
        self.assertIn('$(touch /tmp/no) moon', kwargs['stdin'])
        self.assertNotIn('ANTHROPIC_API_KEY', kwargs['env'])
        self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', kwargs['env'])

    def test_invalid_topics_do_not_start_cli(self):
        for topic in ['', ' '*4, 'x'*501]:
            self.assertEqual(self.client.post('/generate', headers=self.headers, json={'topic':topic}).status_code, 422)
        self.assertEqual(self.calls, [])

    def test_retired_terminal_cannot_start_cli(self):
        from starlette.websockets import WebSocketDisconnect
        with self.assertRaises(WebSocketDisconnect):
            with self.client.websocket_connect('/terminal'):
                pass
        self.assertEqual(self.calls, [])

    def test_missing_secret_fails_closed(self):
        with patch.dict(os.environ, {'STUDIO_ACCESS':''}):
            self.assertEqual(self.client.get('/status',headers=self.headers).status_code, 503)


if __name__ == '__main__':
    unittest.main()
