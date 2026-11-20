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

TOKEN = 'plan-token-123456789012345'


def jpeg():
    out = io.BytesIO()
    Image.new('RGB', (900, 1600), (40, 80, 120)).save(out, 'JPEG')
    return out.getvalue()


class Response(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def studio(**ai):
    return {'connectors': {'ai': {'images': True, 'clips': False, 'maxImages': 4, 'maxClips': 1, 'credits': 100, **ai},
                           'voice': {'enabled': True, 'budget': 5}}}


class ConnectorTests(unittest.TestCase):
    def test_settings_are_clamped_with_safe_defaults(self):
        self.assertEqual(cn.settings({})['ai'], cn.DEFAULTS['ai'])
        odd = cn.settings({'connectors': {'ai': {'maxImages': 99, 'credits': -5}, 'voice': {'en': 'bad id!', 'ar': 'abcDEF123456'}}})
        self.assertEqual((odd['ai']['maxImages'], odd['ai']['credits']), (8, 0))
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

    def test_allowance_follows_switches_limits_and_credits_left(self):
        self.assertEqual(cn.Connectors(studio(), {}, plan_token=TOKEN).allowance(), {'images': 4, 'clips': 0})
        self.assertEqual(cn.Connectors(studio(clips=True), {}, plan_token=TOKEN).allowance(), {'images': 4, 'clips': 1})
        self.assertEqual(cn.Connectors(studio(clips=True, credits=5), {}, plan_token=TOKEN).allowance(), {'images': 4, 'clips': 0})
        self.assertEqual(cn.Connectors(studio(credits=0.5), {}, plan_token=TOKEN).allowance(), {'images': 2, 'clips': 0})
        # Not signed in, or AI switched off: nothing.
        self.assertEqual(cn.Connectors(studio(), {}).allowance(), {'images': 0, 'clips': 0})
        self.assertIsNone(cn.Connectors(studio(images=False, clips=False), {}, plan_token=TOKEN).higgsfield)
        self.assertEqual(cn.Connectors(studio(), {}).prompt(), '')
        prompt = cn.Connectors(studio(), {}, plan_token=TOKEN).prompt()
        self.assertIn('up to 4 AI images and 0 AI video clips', prompt)
        self.assertIn('prophet', prompt)

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


class FakeMcp:
    """Answers like mcp.higgsfield.ai: JSON-RPC over HTTP, sometimes as a server-sent event stream."""
    JOB_IMAGE, JOB_CLIP = '0a1b2c3d-1111-4222-8333-944455556666', '0a1b2c3d-1111-4222-8333-944455557777'

    def __init__(self, sse=False):
        self.sse, self.calls, self.waits = sse, [], 0

    def __call__(self, request, timeout=None):
        url = urlsplit(request.full_url)
        if url.netloc == 'cdn.higgsfield.test':
            return Response(jpeg())
        assert request.get_header('Authorization') == 'Bearer plan-token-123456789012345'
        message = json.loads(request.data)
        method = message.get('method')
        if method == 'notifications/initialized':
            return self.reply(None)
        if method == 'initialize':
            return self.reply({'jsonrpc': '2.0', 'id': message['id'], 'result': {'protocolVersion': '2025-06-18'}},
                              session='sess-1')
        assert request.get_header('Mcp-session-id') == 'sess-1'
        name, args = message['params']['name'], message['params']['arguments']
        self.calls.append((name, args))
        if name in ('generate_image_batch', 'generate_video_batch'):
            job = self.JOB_IMAGE if name == 'generate_image_batch' else self.JOB_CLIP
            body = {'jobs': [{'index': 0, 'job_id': job}]}
        else:
            self.waits += 1
            job = args['jobs'][0]['job_id']
            done = self.waits % 2 == 0
            body = {'all_terminal': done, 'poll_after_seconds': 2, 'jobs': [{'index': 0, 'job_id': job,
                    'status': 'completed' if done else 'in_progress',
                    **({'results': [{'url': 'https://cdn.higgsfield.test/' + job}]} if done else {})}]}
        return self.reply({'jsonrpc': '2.0', 'id': message['id'],
                           'result': {'content': [{'type': 'text', 'text': json.dumps(body)}], 'isError': False}})

    def reply(self, payload, session=''):
        if payload is None:
            response = Response(b'')
        elif self.sse:
            response = Response(('event: message\ndata: ' + json.dumps(payload) + '\n\n').encode())
        else:
            response = Response(json.dumps(payload).encode())
        response.headers = {'Mcp-Session-Id': session} if session else {}
        return response


class PlanTests(unittest.TestCase):
    TOKEN = 'plan-token-123456789012345'

    def test_plan_images_use_credits_through_the_mcp(self):
        for sse in (False, True):
            fake = FakeMcp(sse=sse)
            conn = cn.Connectors(studio(), {}, opener=fake, sleep=lambda s: None, plan_token=self.TOKEN)
            self.assertIsInstance(conn.higgsfield, cn.HiggsfieldPlan)
            # 100 credits by default: images are limited by the film's own maximum, not the money.
            self.assertEqual(conn.allowance(), {'images': 4, 'clips': 0})
            conn.prompt()
            with tempfile.TemporaryDirectory() as root:
                credit = conn.media({'id': 'tent', 'kind': 'image', 'source': 'ai', 'query': 'a desert tent'}, Path(root) / 'tent.png')
                self.assertTrue((Path(root) / 'tent.png').is_file())
            self.assertEqual(credit['source'], 'AI')
            name, args = fake.calls[0]
            self.assertEqual(name, 'generate_image_batch')
            self.assertEqual(args['requests'][0]['params'], {'model': cn.PLAN_IMAGE_MODEL, 'prompt': 'a desert tent',
                                                             'aspect_ratio': '9:16', 'use_unlim': False})
            self.assertEqual(conn.summary(), {'higgsfield_credits': cn.PLAN_IMAGE_CREDITS})

    def test_plan_clips_start_from_the_generated_image(self):
        fake = FakeMcp()
        conn = cn.Connectors(studio(clips=True), {}, opener=fake, sleep=lambda s: None, plan_token=self.TOKEN)
        conn.prompt()
        from unittest.mock import patch
        with tempfile.TemporaryDirectory() as root, patch('resources.normalize_media'):
            conn.media({'id': 'walk', 'kind': 'video', 'query': 'a caravan crossing dunes'}, Path(root) / 'walk.mp4')
        video = [args for name, args in fake.calls if name == 'generate_video_batch'][0]['requests'][0]['params']
        self.assertEqual(video['medias'], [{'value': FakeMcp.JOB_IMAGE, 'role': 'start_image'}])
        self.assertEqual((video['model'], video['sound'], video['use_unlim']), (cn.PLAN_CLIP_MODEL, 'off', False))
        self.assertAlmostEqual(conn.summary()['higgsfield_credits'], cn.PLAN_IMAGE_CREDITS + cn.PLAN_CLIP_CREDITS)

    def test_credit_limit(self):
        few = cn.Connectors(studio(clips=True, credits=7), {}, plan_token=self.TOKEN)
        self.assertEqual(few.allowance(), {'images': 2, 'clips': 1})  # 6.5 credits for the clip, 0.5 left: 2 images

    def test_refusals_and_balance_questions_are_explained(self):
        class Refusing(FakeMcp):
            def __call__(self, request, timeout=None):
                message = json.loads(request.data)
                if message.get('method') == 'tools/call':
                    return self.reply({'jsonrpc': '2.0', 'id': message['id'], 'result': {
                        'content': [{'type': 'text', 'text': 'Not enough credits'}], 'isError': True}})
                return super().__call__(request, timeout)
        conn = cn.Connectors(studio(), {}, opener=Refusing(), sleep=lambda s: None, plan_token=self.TOKEN)
        conn.prompt()
        with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(cn.ConnectorError, 'Not enough credits'):
            conn.media({'id': 'x', 'kind': 'image', 'query': 'something'}, Path(root) / 'x.png')


class RepairTests(unittest.TestCase):
    def test_long_ai_shot_descriptions_are_accepted(self):
        from production import parse_design
        from test_production import design
        long = 'A wide, low-angle shot of an icy city sidewalk at dusk, ' * 20
        film = {**design(), 'assets': [{'id': 'ice', 'kind': 'image', 'source': 'ai', 'query': long[:900]}]}
        self.assertEqual(len(parse_design(json.dumps(film))['assets'][0]['query']), 900)

    def test_the_repair_request_says_what_was_wrong_and_a_second_failure_is_explained(self):
        from production import ProductionManager
        from test_production import design
        prompts = []
        bad = {**design(), 'assets': [{'id': 'x', 'kind': 'image', 'source': 'stock', 'query': 'calm sea'}]}

        async def author(prompt):
            prompts.append(prompt)
            return json.dumps(bad)

        async def scenario(root):
            manager = ProductionManager(root, author, object(), render=lambda *a: None, sound=None)
            job = manager.start('Ice', 30, 'auto')
            await manager.task
            return manager.status(job['id'])
        with tempfile.TemporaryDirectory() as root:
            state = asyncio.run(scenario(root))
        self.assertEqual(len(prompts), 2)
        self.assertIn('assets.0.source', prompts[1])
        self.assertEqual(state['state'], 'failed')
        self.assertIn('did not pass the checks twice', state['message'])
        self.assertIn('assets.0.source', state['message'])
