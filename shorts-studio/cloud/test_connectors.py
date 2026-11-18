import asyncio
import base64
import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import urlsplit

from PIL import Image

import connectors as cn

KEY = 'kid-1234:secret-5678'


def jpeg():
    out = io.BytesIO()
    Image.new('RGB', (900, 1600), (40, 80, 120)).save(out, 'JPEG')
    return out.getvalue()


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


class FakeHiggsfield:
    """Answers like api.higgsfield.ai: queued, then completed with a file URL."""

    def __init__(self, end='completed'):
        self.end, self.posts, self.polls = end, [], 0

    def __call__(self, request, timeout=None):
        url = urlsplit(request.full_url)
        if url.netloc == 'cdn.higgsfield.test':
            return Response(jpeg())
        assert request.get_header('Authorization') == 'Key ' + KEY
        if request.get_method() == 'POST' and url.path.startswith('/requests/'):
            return Response(b'{}')
        if request.get_method() == 'POST':
            self.posts.append((url.path, json.loads(request.data)))
            return Response(json.dumps({'status': 'queued', 'request_id': '11111111-2222-3333-4444-555555555555'}).encode())
        self.polls += 1
        if self.polls == 1:
            return Response(json.dumps({'status': 'in_progress'}).encode())
        done = {'status': self.end, 'error': 'bad prompt' if self.end != 'completed' else None}
        done.update(images=[{'url': 'https://cdn.higgsfield.test/a.jpg'}], video={'url': 'https://cdn.higgsfield.test/a.mp4'})
        return Response(json.dumps(done).encode())


def studio(**ai):
    return {'connectors': {'ai': {'images': True, 'clips': False, 'maxImages': 4, 'maxClips': 1, 'budget': 10, **ai},
                           'voice': {'enabled': True, 'budget': 5}}}


class ConnectorTests(unittest.TestCase):
    def test_settings_are_clamped_with_safe_defaults(self):
        self.assertEqual(cn.settings({})['ai'], cn.DEFAULTS['ai'])
        odd = cn.settings({'connectors': {'ai': {'maxImages': 99, 'budget': -5}, 'voice': {'en': 'bad id!', 'ar': 'abcDEF123456'}}})
        self.assertEqual((odd['ai']['maxImages'], odd['ai']['budget']), (8, 0))
        self.assertEqual((odd['voice']['en'], odd['voice']['ar']), ('', 'abcDEF123456'))

    def test_budget_counts_this_month_and_reports_each_charge(self):
        reported = []
        budget = cn.Budget({'higgsfield': 1.0}, {'month': cn.month(), 'higgsfield': 0.7}, lambda s, a: reported.append((s, a)))
        self.assertAlmostEqual(budget.left('higgsfield'), 0.3)
        self.assertFalse(budget.allows('higgsfield', 0.5))
        budget.charge('higgsfield', 0.06)
        self.assertEqual(reported, [('higgsfield', 0.06)])
        # Last month's spending does not count.
        self.assertEqual(cn.Budget({'higgsfield': 1.0}, {'month': '2000-01', 'higgsfield': 9}).left('higgsfield'), 1.0)

    def test_allowance_follows_switches_limits_and_money_left(self):
        source = {'HIGGSFIELD_API_KEY': KEY}
        self.assertEqual(cn.Connectors(studio(), source).allowance(), {'images': 4, 'clips': 0})
        self.assertEqual(cn.Connectors(studio(clips=True), source).allowance(), {'images': 4, 'clips': 1})
        poor = cn.Connectors(studio(clips=True, budget=0.3), source)
        self.assertEqual(poor.allowance(), {'images': 4, 'clips': 0})
        self.assertEqual(cn.Connectors(studio(budget=0.1), source).allowance(), {'images': 1, 'clips': 0})
        self.assertEqual(cn.Connectors(studio(), {}).allowance(), {'images': 0, 'clips': 0})
        self.assertEqual(cn.Connectors(studio(), {}).prompt(), '')
        prompt = cn.Connectors(studio(), source).prompt()
        self.assertIn('up to 4 AI images and 0 AI video clips', prompt)
        self.assertIn('prophet', prompt)

    def test_ai_image_and_clip_are_generated_downloaded_and_charged(self):
        fake = FakeHiggsfield()
        conn = cn.Connectors(studio(clips=True, maxImages=1), {'HIGGSFIELD_API_KEY': KEY}, opener=fake, sleep=lambda s: None)
        conn.prompt()
        with tempfile.TemporaryDirectory() as root:
            credit = conn.media({'id': 'tent', 'kind': 'image', 'source': 'ai', 'query': 'a desert tent at dusk'}, Path(root) / 'tent.png')
            self.assertTrue((Path(root) / 'tent.png').is_file())
            self.assertEqual(credit['source'], 'AI')
            self.assertEqual(fake.posts[0], (cn.IMAGE_MODEL, {'prompt': 'a desert tent at dusk', 'aspect_ratio': '9:16',
                                                              'resolution': '2K', 'num_images': 1}))
            # The film was granted one image: a second one is refused before anything is spent.
            with self.assertRaisesRegex(cn.ConnectorError, 'No AI images left'):
                conn.media({'id': 'more', 'kind': 'image', 'query': 'another'}, Path(root) / 'more.png')
        self.assertEqual(conn.summary(), {'higgsfield': cn.IMAGE_COST})

    def test_clip_requests_use_the_generated_image(self):
        fake = FakeHiggsfield()
        conn = cn.Connectors(studio(clips=True), {'HIGGSFIELD_API_KEY': KEY}, opener=fake, sleep=lambda s: None)
        conn.prompt()
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root, patch('resources.normalize_media') as normalize:
            conn.media({'id': 'walk', 'kind': 'video', 'query': 'a caravan crossing dunes'}, Path(root) / 'walk.mp4')
        self.assertEqual(fake.posts[1][0], cn.CLIP_MODEL)
        self.assertEqual(fake.posts[1][1]['image_url'], 'https://cdn.higgsfield.test/a.jpg')
        self.assertEqual(normalize.call_args[0][2], 'video')
        self.assertAlmostEqual(conn.summary()['higgsfield'], cn.IMAGE_COST + cn.CLIP_COST)

    def test_refused_generations_are_explained(self):
        conn = cn.Connectors(studio(), {'HIGGSFIELD_API_KEY': KEY}, opener=FakeHiggsfield('nsfw'), sleep=lambda s: None)
        conn.prompt()
        with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(cn.ConnectorError, 'nsfw'):
            conn.media({'id': 'x', 'kind': 'image', 'query': 'something'}, Path(root) / 'x.png')
        with self.assertRaisesRegex(cn.ConnectorError, 'key-id:key-secret'):
            cn.Higgsfield('just-one-part')

    def test_eleven_words_follow_the_character_timings(self):
        alignment = {'characters': list('Hi there'), 'character_start_times_seconds': [0, .1, .2, .3, .4, .5, .6, .7],
                     'character_end_times_seconds': [.1, .2, .3, .4, .5, .6, .7, .8]}
        self.assertEqual(cn.eleven_words(alignment), [{'start': 0, 'end': 0.2, 'text': 'Hi'},
                                                      {'start': 0.3, 'end': 0.8, 'text': 'there'}])

    def test_premium_voice_writes_audio_and_word_timings(self):
        calls = []

        def eleven(request, timeout=None):
            calls.append((request.full_url, request.get_header('Xi-api-key'), json.loads(request.data)))
            return Response(json.dumps({'audio_base64': base64.b64encode(b'\xff' * 4000).decode(), 'alignment': {
                'characters': list('Salam'), 'character_start_times_seconds': [0, .1, .2, .3, .4],
                'character_end_times_seconds': [.1, .2, .3, .4, .5]}}).encode())
        config = {'connectors': {'voice': {'enabled': True, 'ar': 'ArabicVoice123', 'budget': 5}}}
        conn = cn.Connectors(config, {'ELEVENLABS_API_KEY': 'k' * 32}, opener=eleven)
        synth = conn.voice()
        with tempfile.TemporaryDirectory() as root:
            out = Path(root) / 'beat.mp3'
            asyncio.run(synth('Salam', 'ar-SA-HamedNeural', out))
            self.assertEqual(out.stat().st_size, 4000)
            self.assertEqual(json.loads(out.with_suffix('.words.json').read_text())[0]['text'], 'Salam')
        self.assertIn('/v1/text-to-speech/ArabicVoice123/with-timestamps', calls[0][0])
        self.assertEqual(calls[0][2]['model_id'], cn.ELEVEN_MODEL)
        self.assertAlmostEqual(conn.summary()['elevenlabs'], 5 * cn.VOICE_COST_PER_CHAR)
        # No key, switched off, or no money left: the free voices are used.
        self.assertIsNone(cn.Connectors(config, {}).voice())
        self.assertIsNone(cn.Connectors({'connectors': {'voice': {'enabled': False}}}, {'ELEVENLABS_API_KEY': 'k' * 32}).voice())

    def test_a_failing_premium_voice_falls_back(self):
        from production import VoiceUnavailable

        def down(request, timeout=None):
            raise OSError('connection reset')
        synth = cn.Connectors({}, {'ELEVENLABS_API_KEY': 'k' * 32}, opener=down).voice()
        with tempfile.TemporaryDirectory() as root, self.assertRaises(VoiceUnavailable):
            asyncio.run(synth('Hello', 'en-US-GuyNeural', Path(root) / 'b.mp3'))

    def test_nothing_is_set_up_without_keys(self):
        self.assertIsNone(cn.from_environment({}, {}))


if __name__ == '__main__':
    unittest.main()


class FilmWithConnectorsTests(unittest.TestCase):
    def test_film_uses_ai_assets_and_the_premium_voice_and_discloses_ai(self):
        from production import ProductionManager
        from test_production import design
        prompts, voices = [], []

        class Fake:
            def prompt(self):
                return '\nAI GENERATION: up to 2 AI images\n'

            def media(self, request, destination):
                Image.new('RGB', (900, 1600)).save(destination, 'PNG')
                return {'title': 'AI illustration: ' + request['query'], 'url': '', 'credit': 'AI-generated with Higgsfield',
                        'license': 'AI-generated', 'asset': 'assets/' + destination.name, 'source': 'AI'}

            def voice(self):
                async def premium(text, voice, output):
                    voices.append(('premium', text))
                    output.write_bytes(text.encode())
                return premium

        async def author(prompt):
            prompts.append(prompt)
            film = design()
            film['assets'] = [{'id': 'tent', 'kind': 'image', 'source': 'ai', 'query': 'a tent at dusk'}]
            film['source'] = film['source'].replace('export default', "const t = 'assets/tent.png';\nexport default")
            return json.dumps(film)

        async def free_voice(text, voice, output):
            voices.append(('free', text))
            output.write_bytes(text.encode())
        seen = {}

        def render(image, film, audio, seconds, output):
            seen.update(film)
            output.write_bytes(b'video')

        async def scenario(root):
            manager = ProductionManager(root, author, object(), synth=free_voice, render=render, probe=lambda p: 2.0,
                                        join=lambda parts, gap, out: out.write_bytes(b'j'), connectors=Fake(), sound=None)
            job = manager.start('A desert story', 30, 'auto', options={'endCard': False})
            await manager.task
            return manager.status(job['id'])
        with tempfile.TemporaryDirectory() as root:
            state = asyncio.run(scenario(root))
        self.assertEqual(state['state'], 'ready', state.get('message'))
        self.assertIn('AI GENERATION', prompts[0])
        self.assertTrue(seen['aiMedia'])
        self.assertTrue(voices and all(kind == 'premium' for kind, _ in voices))
        self.assertIn('AI-generated with Higgsfield', json.dumps(seen['sources']))


class DisclosureTests(unittest.TestCase):
    def test_youtube_is_told_when_a_film_has_ai_media(self):
        from unittest.mock import MagicMock, patch
        from test_production import design
        from youtube import YouTube
        bodies = []
        service = MagicMock()

        def insert(part, body, media_body):
            bodies.append(body)
            request = MagicMock()
            request.next_chunk.return_value = (None, {'id': 'vid1'})
            return request
        service.videos.return_value.insert.side_effect = insert

        class Store(dict):
            def put(self, key, value, skip_if_exists=False):
                self[key] = value
                return True
        with tempfile.TemporaryDirectory() as root, \
                patch('google.oauth2.credentials.Credentials.from_authorized_user_info',
                      return_value=MagicMock(expired=False, valid=True)), \
                patch('googleapiclient.discovery.build', return_value=service), \
                patch('googleapiclient.http.MediaFileUpload'):
            video = Path(root) / 'v.mp4'
            video.write_bytes(b'v')
            for ai, job in ((True, 'a' * 32), (False, 'b' * 32)):
                YouTube(Store({'youtube-token': {'refresh_token': 'r'}})).upload(job, video, {**design(), 'aiMedia': ai}, 'public')
        self.assertEqual([b['status']['containsSyntheticMedia'] for b in bodies], [True, False])
