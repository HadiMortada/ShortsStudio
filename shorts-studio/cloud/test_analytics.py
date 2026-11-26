import io
import json
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import analytics

NOW = datetime(2026, 11, 20, 12, tzinfo=timezone.utc)
RAW = json.dumps({'client_id': 'id', 'client_secret': 's', 'refresh_token': 'r'})
VIDEOS = {'aaaaaaaaaaa': ('Why ice floats', 50_000, '2026-11-10T16:00:00Z'),
          'bbbbbbbbbbb': ('Static shock', 900, '2026-11-15T16:00:00Z'),
          'ccccccccccc': ('Brand new', 40, '2026-11-20T06:00:00Z')}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeGoogle:
    def __init__(self, analytics_error=None):
        self.analytics_error, self.calls = analytics_error, []

    def __call__(self, request, timeout=None):
        url = urlsplit(request.full_url)
        query = parse_qs(url.query)
        self.calls.append((url.netloc + url.path, query))
        if url.netloc == 'oauth2.googleapis.com':
            return Response(json.dumps({'access_token': 'tok', 'scope': 'https://www.googleapis.com/auth/youtube.readonly'}).encode())
        if url.netloc == 'youtubeanalytics.googleapis.com':
            if self.analytics_error:
                raise HTTPError(request.full_url, 403, 'Forbidden', {}, io.BytesIO(self.analytics_error.encode()))
            if query['dimensions'][0] == 'video':
                head = [{'name': 'video'}] + [{'name': m} for m in analytics.METRICS]
                rows = [[v, VIDEOS[v][1], 10, 20 if v == 'aaaaaaaaaaa' else 6, 75 if v == 'aaaaaaaaaaa' else 22, 5, 3, 7, 1]
                        for v in query['filters'][0][len('video=='):].split(',') if v in VIDEOS]
                return Response(json.dumps({'columnHeaders': head, 'rows': rows}).encode())
            good = query['filters'][0].endswith('aaaaaaaaaaa')
            head = [{'name': 'elapsedVideoTimeRatio'}, {'name': 'audienceWatchRatio'}, {'name': 'relativeRetentionPerformance'}]
            rows = [[r / 100, (1.1 if good else 0.9) - r / 100 * (0.3 if good else 0.8), 0.7 if good else 0.2] for r in range(1, 101)]
            return Response(json.dumps({'columnHeaders': head, 'rows': rows}).encode())
        items = [{'id': v, 'snippet': {'title': VIDEOS[v][0], 'description': '', 'channelId': 'me', 'channelTitle': 'me',
                                       'publishedAt': VIDEOS[v][2]},
                  'statistics': {'viewCount': str(VIDEOS[v][1]), 'likeCount': '10', 'commentCount': '2'},
                  'contentDetails': {'duration': 'PT30S'}} for v in query['id'][0].split(',') if v in VIDEOS]
        return Response(json.dumps({'items': items}).encode())


FILMS = [{'run': '3', 'youtube': 'https://www.youtube.com/shorts/ccccccccccc', 'title': 'Brand new', 'createdAt': 0},
         {'run': '2', 'youtube': 'https://youtube.com/shorts/bbbbbbbbbbb', 'brief': 'Static shock explained',
          'profile': {'hook': 'Ever been zapped by a doorknob?', 'topic': 'Static shock'}},
         {'run': '1', 'youtube': 'https://www.youtube.com/shorts/aaaaaaaaaaa', 'hashtags': ['#physics'],
          'profile': {'hook': 'Ice should sink. It does not.', 'topic': 'Why ice floats', 'aiMedia': True}},
         {'run': '0', 'title': 'Never uploaded'}]


class AnalyticsTests(unittest.TestCase):
    def test_collect_reads_views_watch_time_and_the_hook(self):
        fake = FakeGoogle()
        rows, problem = analytics.collect(FILMS, {'YOUTUBE_OAUTH_JSON': RAW}, 'Asia/Beirut', opener=fake, now=NOW)
        self.assertEqual(problem, '')
        self.assertEqual([r['title'] for r in rows], ['Brand new', 'Static shock', 'Why ice floats'])
        ice = rows[2]
        self.assertEqual((ice['views'], ice['watchedPercent'], ice['subscribersGained']), (50_000, 75, 7))
        self.assertEqual((ice['hook'], ice['topic'], ice['aiMedia']), ('Ice should sink. It does not.', 'Why ice floats', True))
        self.assertEqual((ice['postedDay'], ice['postedHour']), ('Tuesday', '18:00'))  # Beirut time
        self.assertGreater(ice['viewsPerDay'], rows[1]['viewsPerDay'])
        self.assertGreater(ice['stillWatching']['afterHook'], rows[1]['stillWatching']['afterHook'])
        self.assertNotIn('stillWatching', rows[0])  # under 2 days old: no curve yet
        # One report for all films, then one curve per old enough film.
        reports = [q for host, q in fake.calls if host.startswith('youtubeanalytics')]
        self.assertEqual(len(reports), 3)
        self.assertEqual(reports[0]['ids'], ['channel==MINE'])

    def test_without_the_analytics_permission_views_still_count(self):
        fake = FakeGoogle(analytics_error='{"error": {"message": "insufficient permissions"}}')
        rows, problem = analytics.collect(FILMS, {'YOUTUBE_OAUTH_JSON': RAW}, opener=fake, now=NOW)
        self.assertEqual(len(rows), 3)
        self.assertNotIn('watchedPercent', rows[2])
        self.assertIn('tap Allow', problem)
        fake = FakeGoogle(analytics_error='{"error": {"errors": [{"reason": "accessNotConfigured"}]}}')
        self.assertIn('YouTube Analytics API', analytics.collect(FILMS, {'YOUTUBE_OAUTH_JSON': RAW}, opener=fake, now=NOW)[1])

    def test_films_on_other_channels_use_that_channels_sign_in(self):
        channel = 'UC' + 'x' * 22
        films = [{**FILMS[2], 'channel': channel}]
        rows, _ = analytics.collect(films, {'YOUTUBE_ACCOUNTS_JSON': json.dumps({channel: json.loads(RAW)})}, opener=FakeGoogle(), now=NOW)
        self.assertEqual(rows[0]['channel'], channel)
        self.assertEqual(analytics.collect(FILMS, {}, opener=FakeGoogle(), now=NOW), ([], 'Connect YouTube on the page to read your films’ numbers.'))
        self.assertEqual(analytics.collect([FILMS[3]], {'YOUTUBE_OAUTH_JSON': RAW})[0], [])

    def test_lessons_are_cleaned_and_reach_the_prompts(self):
        rows, _ = analytics.collect(FILMS, {'YOUTUBE_OAUTH_JSON': RAW}, opener=FakeGoogle(), now=NOW)
        prompt = analytics.lessons_prompt(rows, {'domain': 'Physics'})
        self.assertIn('Ice should sink', prompt)
        self.assertIn('afterHook', prompt)
        raw = {'summary': 'Surprising questions win.', 'rules': ['Open with a surprising question', ''],
               'working': [{'lesson': 'Myth-busting', 'evidence': '50K vs 900 views'}, 'junk'],
               'moreTopics': ['Everyday myths'], 'hooks': ['Ice should sink. It does not.'], 'bestSeconds': 30}
        lessons = analytics.clean_lessons(raw, rows, 'Reconnect YouTube')
        self.assertEqual(lessons['rules'], ['Open with a surprising question'])
        self.assertEqual(lessons['working'], [{'lesson': 'Myth-busting', 'evidence': '50K vs 900 views'}])
        self.assertTrue(lessons['early'])
        self.assertEqual(lessons['table'][0]['title'], 'Why ice floats')
        with self.assertRaises(ValueError):
            analytics.clean_lessons({'summary': 'x', 'rules': []}, rows)
        with tempfile.TemporaryDirectory() as d:
            path = Path(d, 'lessons.json')
            self.assertEqual(analytics.lessons_block(path), '')
            path.write_text(json.dumps(lessons))
            block = analytics.lessons_block(path)
            self.assertIn('- Open with a surprising question', block)
            self.assertIn('early signs', block)
            self.assertIn('Best length so far: about 30 seconds', block)

    def test_the_library_keeps_what_each_film_was(self):
        from actions_deliver import film_profile
        with tempfile.TemporaryDirectory() as d:
            Path(d, 'topic.json').write_text(json.dumps({'topic': 'Why ice floats', 'angle': 'Ice breaks the rule'}))
            profile = film_profile({'beats': ['Ice should sink.', 'It does not.'], 'aiMedia': True}, Path(d))
        self.assertEqual(profile, {'topic': 'Why ice floats', 'angle': 'Ice breaks the rule', 'hook': 'Ice should sink.',
                                   'scenes': 2, 'aiMedia': True, 'voiceNote': ''})


    def test_the_film_job_reads_the_latest_numbers_without_creating_anything(self):
        from unittest.mock import patch
        calls = []

        class FakeLibrary:
            NAME = 'Shorts Studio films'

            def __init__(self, repo):
                self.release = None

            def json(self, method, path, body=None):
                calls.append((method, path))
                return [{'draft': True, 'name': 'Shorts Studio films', 'id': 7}] if drafts else []

            def index(self):
                return FILMS
        with tempfile.TemporaryDirectory() as d, patch('actions_deliver.Library', FakeLibrary):
            out = Path(d, 'numbers.json')
            drafts = True
            with patch('analytics.collect', return_value=([{'title': 'Ice', 'viewsPerDay': 5}], '')) as collect:
                data = analytics.snapshot(out, {'GITHUB_REPOSITORY': 'me/repo'}, Path(d, 'none.json'))
            self.assertEqual(collect.call_args[0][0], FILMS)
            self.assertEqual(json.loads(out.read_text())['rows'], [{'title': 'Ice', 'viewsPerDay': 5}])
            self.assertEqual(data['rows'][0]['title'], 'Ice')
            self.assertTrue(all(method == 'GET' for method, _ in calls))  # never creates the library
            out.unlink()
            drafts = False
            self.assertIsNone(analytics.snapshot(out, {'GITHUB_REPOSITORY': 'me/repo'}, Path(d, 'none.json')))
            self.assertFalse(out.exists())
            self.assertIsNone(analytics.snapshot(out, {}, Path(d, 'none.json')))  # no repository: skipped, never fails
        with patch('analytics.snapshot') as snap, patch('analytics.main') as weekly:
            analytics.cli(['--snapshot', 'x.json'])
            analytics.cli([])
        snap.assert_called_once_with('x.json')
        weekly.assert_called_once()


if __name__ == '__main__':
    unittest.main()
