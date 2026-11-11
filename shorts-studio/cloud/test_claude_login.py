import json
import unittest
from types import SimpleNamespace

import claude_login as cl

URL = ('https://claude.com/cai/oauth/authorize?code=true&client_id=9d1c250a&response_type=code'
       '&scope=user%3Ainference&code_challenge=abc&code_challenge_method=S256&state=xyz')
TOKEN = 'sk-ant-oat01-' + 'A1b2_C3d4-' * 6
NONCE = 'ab' * 16


class FakeVariables:
    def __init__(self, code=None):
        self.values, self.states, self.code = {}, [], code

    def get(self, name):
        if name == cl.CODE and self.code and self.states and self.states[-1]['state'] == 'link':
            return {'nonce': NONCE, 'code': self.code}
        return self.values.get(name)

    def set(self, name, value):
        self.values[name] = value
        if name == cl.STATE:
            self.states.append(value)

    def delete(self, name):
        self.values.pop(name, None)
        if name == cl.CODE:
            self.code = None


class FakeTerminal:
    def __init__(self, before, after):
        self.text, self.after, self.typed, self.closed = before, after, [], False

    def read(self, seconds):
        return self.text

    def wait_for(self, find, seconds):
        return find(self.text)

    def type(self, value):
        self.typed.append(value)
        self.text += self.after

    def close(self):
        self.closed = True


class LoginTests(unittest.TestCase):
    def test_sign_in_link_is_found_even_when_wrapped(self):
        raw = ('\x1b[1mThis will guide you\x1b[0m\r\nBrowser didn\'t open? Use the url below\r\n\r\n'
               + URL[:70] + '\r\n' + URL[70:] + '\r\n\r\nPaste code here if prompted >')
        self.assertEqual(cl.sign_in_url(cl.clean(raw.encode())), URL)
        self.assertIsNone(cl.sign_in_url('https://claude.evil.com/oauth/authorize?x=1\n\n'))
        self.assertIsNone(cl.sign_in_url('no link yet'))

    def test_successful_renewal_saves_the_token_without_publishing_it(self):
        variables = FakeVariables(code='Code_12345-abc#state')
        terminal = FakeTerminal(URL + '\n\nPaste code here >', '\nYour OAuth token (valid for 1 year):\n' + TOKEN + '\n')
        saved = []
        self.assertTrue(cl.renew(NONCE, variables, saved.append, lambda: terminal, poll=0))
        self.assertEqual(saved, [TOKEN])
        self.assertEqual(terminal.typed, ['Code_12345-abc#state'])
        self.assertEqual([s['state'] for s in variables.states], ['starting', 'link', 'checking', 'saving', 'done'])
        self.assertEqual(variables.states[1]['url'], URL)
        self.assertNotIn(TOKEN, json.dumps(variables.states))
        self.assertTrue(terminal.closed)
        self.assertNotIn(cl.CODE, variables.values)

    def test_rejected_code_reports_failure(self):
        variables = FakeVariables(code='Wrong_code_12345')
        terminal = FakeTerminal(URL + '\n\n', '\nOAuth error: Invalid code\n')
        saved = []
        self.assertFalse(cl.renew(NONCE, variables, saved.append, lambda: terminal, poll=0))
        self.assertEqual(saved, [])
        self.assertEqual(variables.states[-1]['state'], 'failed')
        self.assertIn('did not accept', variables.states[-1]['message'])

    def test_malformed_code_is_never_typed(self):
        variables = FakeVariables(code='bad code; rm -rf /')
        terminal = FakeTerminal(URL + '\n\n', '')
        self.assertFalse(cl.renew(NONCE, variables, lambda t: None, lambda: terminal, poll=0))
        self.assertEqual(terminal.typed, [])
        self.assertIn('does not look like', variables.states[-1]['message'])

    def test_missing_link_and_missing_code_time_out_cleanly(self):
        variables = FakeVariables()
        self.assertFalse(cl.renew(NONCE, variables, lambda t: None, lambda: FakeTerminal('starting…', ''), poll=0))
        self.assertIn('sign-in link', variables.states[-1]['message'])
        variables = FakeVariables()
        self.assertFalse(cl.renew(NONCE, variables, lambda t: None, lambda: FakeTerminal(URL + '\n\n', ''),
                                  wait_code=0.05, poll=0))
        self.assertIn('No code arrived', variables.states[-1]['message'])

    def test_codes_for_another_request_are_ignored(self):
        variables = FakeVariables(code='Code_12345-abc#state')
        variables.get = lambda name: {'nonce': 'ff' * 16, 'code': 'Code_12345-abc#state'} if name == cl.CODE else None
        terminal = FakeTerminal(URL + '\n\n', '')
        self.assertFalse(cl.renew(NONCE, variables, lambda t: None, lambda: terminal, wait_code=0.05, poll=0))
        self.assertEqual(terminal.typed, [])

    def test_variables_create_when_missing(self):
        calls = []

        def call(method, path, body=None):
            calls.append((method, path, body))
            if method == 'PATCH':
                return 404, None
            if method == 'GET':
                return 200, {'value': json.dumps({'nonce': NONCE})}
            return 201, None
        variables = cl.Variables('o/r', 't', call)
        variables.set(cl.STATE, {'state': 'link'})
        self.assertEqual([c[0] for c in calls], ['PATCH', 'POST'])
        self.assertEqual(calls[1][2]['name'], cl.STATE)
        self.assertEqual(variables.get(cl.CODE), {'nonce': NONCE})
        with self.assertRaisesRegex(RuntimeError, 'Variables permission'):
            cl.Variables('o/r', 't', lambda *a, **k: (403, None)).set(cl.STATE, {})

    def test_secret_is_saved_through_stdin(self):
        seen = {}

        def run(args, **kw):
            seen.update(args=args, **kw)
            return SimpleNamespace(returncode=0)
        cl.save_secret('o/r', 'ghp', TOKEN, run=run)
        self.assertEqual(seen['args'], ['gh', 'secret', 'set', 'CLAUDE_CODE_OAUTH_TOKEN', '--repo', 'o/r'])
        self.assertNotIn(TOKEN, ' '.join(seen['args']))
        self.assertEqual(seen['input'], TOKEN)
        self.assertEqual(seen['env']['GH_TOKEN'], 'ghp')
        with self.assertRaisesRegex(RuntimeError, 'Secrets'):
            cl.save_secret('o/r', 'ghp', TOKEN, run=lambda *a, **k: SimpleNamespace(returncode=1))


if __name__ == '__main__':
    unittest.main()
