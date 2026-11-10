"""Exercise phone login across separate HTTP requests with a real PTY child."""
import asyncio
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from modal_bridge import create_service


class PhoneLoginTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.cli = self.root / 'fake-claude'
        self.cli.write_text('''#!/usr/bin/env python3
import os, sys, tty
from pathlib import Path
assert sys.argv[1:] == ['auth', 'login']
tty.setraw(0)
print('https://claude.com/oauth/authorize?state=test-state', flush=True)
print('Paste code here if prompted >', flush=True)
value = b''
while True:
    c = os.read(0, 1)
    if c == b'\\r': break
    value += c
if value == b'test-code#test-state':
    Path(os.environ['HOME'], 'signed-in').write_text('yes')
    print('Authentication complete', flush=True)
else:
    print('Wrong code', flush=True)
    sys.exit(1)
''')
        self.cli.chmod(0o700)
        real_spawn = asyncio.create_subprocess_exec
        async def spawn(*args, **kwargs):
            return await real_spawn(str(self.cli), *args[1:], **kwargs)
        self.spawn_patch = patch('asyncio.create_subprocess_exec', side_effect=spawn)
        self.spawn_patch.start()
        self.secret_patch = patch.dict(os.environ, {'STUDIO_ACCESS': 'test-access-key'})
        self.secret_patch.start()
        async def run(args, **kwargs):
            signed_in = (self.root / 'home' / 'signed-in').exists()
            return 0, '{"loggedIn":' + str(signed_in).lower() + '}', ''
        self.commits = []
        self.context = TestClient(create_service(self.root, run=run, commit=lambda: self.commits.append(True)))
        self.client = self.context.__enter__()
        self.headers = {'Authorization': 'Bearer test-access-key'}

    def tearDown(self):
        self.context.__exit__(None, None, None)
        self.spawn_patch.stop()
        self.secret_patch.stop()
        self.tmp.cleanup()

    def poll(self, predicate):
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            response = self.client.get('/login', headers=self.headers)
            self.assertEqual(response.status_code, 200)
            result = response.json()
            if predicate(result): return result
            time.sleep(.02)
        self.fail('Login did not reach expected state')

    def test_login_survives_page_disconnection_and_accepts_enter(self):
        response = self.client.post('/login/start', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        session = self.poll(lambda j: 'Paste code' in j['output'])
        self.assertTrue(session['active'])
        # The start request has ended; a reopened page recovers the same session.
        reopened = self.client.get('/login', headers=self.headers).json()
        self.assertEqual(reopened['sessionId'], session['sessionId'])
        self.assertEqual(self.client.get('/status', headers=self.headers).json()['busy'], True)
        self.assertEqual(self.client.post('/generate', headers=self.headers, json={'topic':'Moon'}).status_code, 409)
        response = self.client.post('/login/code', headers=self.headers,
            json={'sessionId':session['sessionId'], 'code':'test-code#test-state'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['accepted'])
        finished = self.poll(lambda j: not j['active'])
        self.assertNotIn('test-code#test-state', finished['output'])
        self.assertTrue(self.client.get('/status', headers=self.headers).json()['loggedIn'])
        self.assertTrue(self.commits)
        # An ended session must give a visible error, never silently accept input.
        self.assertEqual(self.client.post('/login/code', headers=self.headers,
            json={'sessionId':session['sessionId'], 'code':'test-code#test-state'}).status_code, 409)

    def test_stale_session_cannot_receive_a_code(self):
        response = self.client.post('/login/start', headers=self.headers)
        self.assertEqual(response.status_code, 200)
        self.poll(lambda j: 'Paste code' in j['output'])
        self.assertEqual(self.client.post('/login/code', headers=self.headers,
            json={'sessionId':'old-session', 'code':'test-code#test-state'}).status_code, 409)
        self.assertFalse((self.root / 'home' / 'signed-in').exists())

    def test_repeated_start_resumes_and_multiline_input_is_rejected(self):
        started = self.client.post('/login/start', headers=self.headers).json()
        resumed = self.client.post('/login/start', headers=self.headers).json()
        self.assertEqual(started['sessionId'],resumed['sessionId'])
        for code in ['one\ntwo', '\x1bsecret', 'x'*1025, '   ']:
            self.assertEqual(self.client.post('/login/code', headers=self.headers,
                json={'sessionId':started['sessionId'],'code':code}).status_code,422)

    def test_login_requires_authentication(self):
        for method, path in [('get','/login'), ('post','/login/start'), ('post','/login/code')]:
            response = getattr(self.client, method)(path)
            self.assertEqual(response.status_code, 401)


if __name__ == '__main__':
    unittest.main()
