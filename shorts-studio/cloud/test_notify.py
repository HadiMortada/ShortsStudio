import json
import unittest

from notify import notify


class Opener:
    def __init__(self, fail=False):
        self.requests, self.fail = [], fail

    def __call__(self, request, timeout):
        if self.fail:
            raise OSError('down')
        self.requests.append(request)
        opener = self

        class Response:
            def __enter__(self):
                return opener
            def __exit__(self, *a):
                return False
        return Response()


class NotifyTests(unittest.TestCase):
    def test_reports_to_the_page_with_the_key(self):
        opener = Opener()
        ok = notify('ready', 'Film ready: Mars', 'Made by Claude', '/#library', opener=opener,
                    source={'STUDIO_PAGE_URL': '{"url": "https://studio.example.workers.dev"}', 'STUDIO_NOTIFY_KEY': 'k'})
        self.assertTrue(ok)
        request = opener.requests[0]
        self.assertEqual(request.full_url, 'https://studio.example.workers.dev/api/notify')
        self.assertEqual(request.headers['Authorization'], 'Bearer k')
        self.assertEqual(json.loads(request.data)['kind'], 'ready')

    def test_missing_setup_or_errors_never_fail_a_job(self):
        self.assertFalse(notify('ready', 'x', source={}))
        self.assertFalse(notify('ready', 'x', opener=Opener(fail=True),
                                source={'STUDIO_PAGE_URL': 'https://s.example', 'STUDIO_NOTIFY_KEY': 'k'}))


if __name__ == '__main__':
    unittest.main()
