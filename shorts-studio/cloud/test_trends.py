import io
import json
import unittest
from datetime import datetime, timezone
from urllib.error import HTTPError
from urllib.parse import parse_qs, urlsplit

import ideas
import trends
from youtube import youtube_pack

READ = 'https://www.googleapis.com/auth/youtube.upload https://www.googleapis.com/auth/youtube.readonly'
VIDEOS = {
    'aaaaaaaaaaa': ('Why ice floats #physics #science #shorts', 'c1', 2_000_000, 'PT40S'),
    'bbbbbbbbbbb': ('Static shock explained #physics #sciencefacts', 'c2', 900_000, 'PT55S'),
    'ccccccccccc': ('Microwave myths #science #mybrand', 'c3', 400_000, 'PT30S'),
    'ddddddddddd': ('Long lecture #physics', 'c4', 5_000_000, 'PT45M'),
    'eeeeeeeeeee': ('Brand only #mybrand #sciencefacts', 'c3', 100_000, 'PT20S'),
}


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeYouTube:
    def __init__(self, scope=READ, insufficient=False):
        self.scope, self.insufficient, self.calls = scope, insufficient, []

    def __call__(self, request, timeout=None):
        url = urlsplit(request.full_url)
        self.calls.append((url.path, parse_qs(url.query)))
        if url.netloc == 'oauth2.googleapis.com':
            return Response(json.dumps({'access_token': 'tok', 'scope': self.scope}).encode())
        if self.insufficient:
            raise HTTPError(request.full_url, 403, 'Forbidden', {}, io.BytesIO(b'{"reason":"insufficientPermissions"}'))
        query = parse_qs(url.query)
        if url.path.endswith('/search'):
            return Response(json.dumps({'items': [{'id': {'videoId': v}} for v in VIDEOS]}).encode())
        items = [{'id': v, 'snippet': {'title': VIDEOS[v][0], 'description': '', 'channelId': VIDEOS[v][1],
                                       'channelTitle': VIDEOS[v][1], 'publishedAt': '2026-11-10T00:00:00Z'},
                  'statistics': {'viewCount': str(VIDEOS[v][2]), 'likeCount': '10'},
                  'contentDetails': {'duration': VIDEOS[v][3]}}
                 for v in query['id'][0].split(',') if v in VIDEOS]
        return Response(json.dumps({'items': items}).encode())


RAW = json.dumps({'client_id': 'id', 'client_secret': 's', 'refresh_token': 'r'})


class TrendTests(unittest.TestCase):
    def test_top_shorts_are_recent_short_and_ranked_by_views(self):
        fake = FakeYouTube()
        videos = trends.top_shorts('physics', 'tok', 'en', opener=fake, now=datetime(2026, 11, 17, tzinfo=timezone.utc))
        self.assertEqual([v['id'] for v in videos], ['aaaaaaaaaaa', 'bbbbbbbbbbb', 'ccccccccccc', 'eeeeeeeeeee'])
        search = fake.calls[0][1]
        self.assertEqual((search['order'][0], search['videoDuration'][0], search['relevanceLanguage'][0]),
                         ('viewCount', 'short', 'en'))
        self.assertEqual(search['publishedAfter'][0], '2026-10-27T00:00:00Z')

    def test_hashtags_ranked_by_views_and_never_one_channel_or_blocked(self):
        videos = trends.top_shorts('physics', 'tok', opener=FakeYouTube())
        tags = [h['tag'] for h in trends.rank_hashtags(videos)]
        self.assertEqual(tags[:2], ['#physics', '#science'])
        self.assertIn('#sciencefacts', tags)
        self.assertNotIn('#mybrand', tags)  # only one channel uses it
        self.assertNotIn('#shorts', tags)
        self.assertNotIn('#physics', [h['tag'] for h in trends.rank_hashtags(videos, avoid=['physics'])])

    def test_upload_only_connection_asks_for_reconnect(self):
        with self.assertRaises(trends.NeedsReconnect):
            trends.access_token(RAW, opener=FakeYouTube(scope='https://www.googleapis.com/auth/youtube.upload'))
        self.assertEqual(trends.access_token(RAW, opener=FakeYouTube()), 'tok')
        with self.assertRaises(trends.NeedsReconnect):
            trends.top_shorts('x', 'tok', opener=FakeYouTube(insufficient=True))

    def test_lane_signals_feed_the_idea_prompt(self):
        fake = FakeYouTube()
        signals = trends.lane_signals({'domain': 'Physics', 'name': 'Physics', 'language': 'Arabic'}, 'tok',
                                      own=trends.own_films(['https://www.youtube.com/shorts/ccccccccccc'], 'tok', fake),
                                      opener=fake)
        self.assertEqual(sum(1 for path, _ in fake.calls if path.endswith('/search')), 1)  # one query, not two
        self.assertEqual(signals['language'], 'ar')
        self.assertEqual(signals['top'][0]['views'], 2_000_000)
        self.assertEqual(signals['own'][0]['title'], VIDEOS['ccccccccccc'][0])
        prompt = ideas.idea_prompt({'domain': 'Physics'}, [], [], signals=signals)
        self.assertIn('2.0M views: "Why ice floats', prompt)
        self.assertIn('#science (', prompt)
        self.assertIn("channel's own recent films", prompt)
        self.assertIn('winning patterns', prompt)
        self.assertNotIn('REAL YOUTUBE', ideas.idea_prompt({'domain': 'Physics'}, [], []))

    def test_backup_prompt_has_no_web_search(self):
        from test_backup import Writer
        writer = Writer(json.dumps({'ideas': [{'topic': f'T{i}', 'signal': 'Similar: 1M views',
                                               'hashtags': ['physics', '#Shorts', '#physics', '#science']} for i in range(3)]}))
        new = ideas.backup_ideas({'domain': 'Physics'}, [], [], writers=[writer])
        self.assertNotIn('Use WebSearch', writer.prompts[0])
        self.assertEqual(new[0]['signal'], 'Similar: 1M views')
        self.assertEqual(new[0]['hashtags'], ['#physics', '#science'])

    def test_film_hashtags_use_the_film_search_phrase(self):
        fake = FakeYouTube()
        tags = trends.film_hashtags({'language': 'en-US', 'youtube': {'tags': ['why ice floats', 'ice']}}, 'tok', opener=fake)
        self.assertEqual(fake.calls[0][1]['q'][0], 'why ice floats')
        self.assertEqual(tags[:2], ['#physics', '#science'])

    def test_trending_hashtags_follow_the_topic_in_the_pack(self):
        pack = youtube_pack({'title': 'Ice', 'language': 'en', 'youtube': {
            'hashtags': ['#ice', '#water'], 'reach': ['#didyouknow'], 'trending': ['#physics', '#science', '#ice']}})
        self.assertEqual(pack['hashtags'][:5], ['#Shorts', '#ice', '#water', '#physics', '#science'])
        self.assertLessEqual(len(pack['hashtags']), 10)

    def test_delivery_adds_film_then_topic_hashtags(self):
        import base64
        from unittest.mock import patch
        import actions_deliver as ad
        saved = {'lanes': {'main': {'hashtags': [{'tag': '#everydayphysics'}, {'tag': '#science'}]}}}

        def api(method, path, data=None):
            return {'sha': 's', 'content': base64.b64encode(json.dumps(saved).encode()).decode()}
        with patch('trends.access_token', return_value='tok'), \
                patch('trends.film_hashtags', return_value=['#physics', '#science']):
            tags = ad.viewed_hashtags({'title': 'Ice'}, {'slot': '2026-11-17T10:05+02:00#main'},
                                      {'YOUTUBE_OAUTH_JSON': RAW}, api)
        self.assertEqual(tags, ['#physics', '#science', '#everydayphysics'])
        self.assertEqual(ad.viewed_hashtags({'title': 'Ice'}, {}, {}, api), [])


if __name__ == '__main__':
    unittest.main()
