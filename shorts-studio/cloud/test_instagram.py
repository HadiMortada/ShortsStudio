import asyncio
import io
import json
import tempfile
import unittest
from pathlib import Path

import actions_film as af
from actions_deliver import Library, deliver
from notify import queue_instagram
from test_actions import DESIGN, FakeContents, fake_author, fake_join, fake_render, fake_synth, review_result
from test_actions import DeliverTests, FakeReleases

IG = '17841400000000001'
SOURCE = {'STUDIO_PAGE_URL': '{"url":"https://studio.example.workers.dev"}', 'STUDIO_NOTIFY_KEY': 'k', 'GITHUB_RUN_ID': '11'}


class Opener:
    def __init__(self, fail=False):
        self.requests, self.fail = [], fail

    def __call__(self, request, timeout=0):
        self.requests.append(request)
        if self.fail:
            raise OSError('page down')
        return io.BytesIO(b'{"queued": 1}')


class InstagramTests(unittest.TestCase):
    def args(self, root, *extra):
        return af.parse_args(['--renderer', root, '--out', str(Path(root) / 'out'), '--ideas', str(Path(root) / 'ideas.json'),
                              '--seed', 'run-1', *extra])

    def test_manual_film_keeps_the_chosen_accounts(self):
        with tempfile.TemporaryDirectory() as root:
            result = asyncio.run(af.create(self.args(root, '--brief', 'Day and night', '--instagram', f'{IG},bad'),
                author=fake_author(DESIGN), review=review_result(True), render=fake_render(),
                synth=fake_synth, probe=lambda f: 3.0, join=fake_join, writers=[]))
            self.assertEqual((result['state'], result['instagram'], result['instagramAt']), ('ready', [IG], None))

    def test_a_film_that_failed_the_review_is_not_posted(self):
        with tempfile.TemporaryDirectory() as root:
            result = asyncio.run(af.create(self.args(root, '--brief', 'x', '--instagram', IG),
                author=fake_author(DESIGN), review=review_result(False), render=fake_render(),
                synth=fake_synth, probe=lambda f: 3.0, join=fake_join, writers=[]))
            self.assertEqual(result['instagram'], [])

    def test_saved_films_are_queued_on_the_page(self):
        opener = Opener()
        with tempfile.TemporaryDirectory() as root:
            folder = DeliverTests().folder(root, publish=False, title='Tides', instagram=[IG], instagramAt='2026-11-12T07:00:00Z')
            result = deliver(folder, api=FakeContents(), source=SOURCE, library=Library('o/r', FakeReleases()),
                             instagram_opener=opener)
        self.assertEqual(result['instagram'], 1)
        request = [r for r in opener.requests if r.full_url.endswith('/api/instagram/queue')][0]
        self.assertEqual(request.headers['Authorization'], 'Bearer k')
        self.assertEqual(json.loads(request.data), {'run': '11', 'accounts': [IG], 'publishAt': '2026-11-12T07:00:00Z'})

    def test_a_queue_failure_keeps_the_film(self):
        with tempfile.TemporaryDirectory() as root:
            folder = DeliverTests().folder(root, publish=False, title='Tides', instagram=[IG])
            releases = FakeReleases()
            result = deliver(folder, api=FakeContents(), source=SOURCE, library=Library('o/r', releases),
                             instagram_opener=Opener(fail=True))
        self.assertNotIn('instagram', result)
        self.assertEqual(len(releases.named('film-11.mp4')), 1)

    def test_queueing_needs_the_page(self):
        with self.assertRaises(RuntimeError):
            queue_instagram('11', [IG], source={})
