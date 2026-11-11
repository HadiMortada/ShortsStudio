import asyncio
import base64
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError

import actions_film as af
from actions_deliver import LedgerStore, RepositoryFile, deliver
from production import RenderFailure

SOURCE = ("import React from 'react'; import {AbsoluteFill,useCurrentFrame} from 'remotion'; "
          "export default function Video(){const f=useCurrentFrame();return <AbsoluteFill>"
          "<svg><circle r={100} cx={200+f} cy={400}/></svg></AbsoluteFill>}")
DESIGN = {'title': 'The moving Earth', 'direction': 'A globe becomes a day/night diagram.',
          'language': 'en', 'narration': 'Earth turns, bringing each place into daylight.',
          'source': SOURCE, 'description': 'Day and night', 'sources': [], 'assets': []}
TOPIC = {'topic': 'Leap years', 'angle': 'Why February changes', 'reason': 'Seasonal searches',
         'evidence': [{'title': 'Search trend', 'url': 'https://example.com/t', 'signal': 'rising'}],
         'brief': 'Explain leap years with a rotating calendar wheel.'}


def fake_author(*outputs, search=False):
    queue = list(outputs)

    async def author(prompt):
        author.prompts.append(prompt)
        author.audit = {'searchUsed': search, 'toolCalls': int(search)}
        return json.dumps(queue.pop(0))
    author.prompts, author.audit = [], {}
    return author


def fake_render(fail_first=False):
    def render(image, design, audio, seconds, destination):
        render.calls += 1
        if fail_first and render.calls == 1:
            raise RenderFailure('TypeError: x is undefined')
        destination.write_bytes(b'v' * 2000)
        destination.with_suffix('.png').write_bytes(b'png')
        (destination.parent / 'review.png').write_bytes(b'png')
    render.calls = 0
    return render


async def fake_synth(text, voice, output):
    output.write_bytes(b'a' * 2000)


def fake_join(parts, gap, output):
    output.write_bytes(b''.join(p.read_bytes() for p in parts))


def review_result(passed):
    async def review(design, sheet):
        return {'passed': passed, 'issues': [] if passed else ['Title overlaps globe'], 'method': 'test'}
    return review


class ClaudeSessionTests(unittest.TestCase):
    def test_claude_only_receives_its_own_token(self):
        source = {'PATH': '/bin', 'CLAUDE_CODE_OAUTH_TOKEN': 'sk-oat', 'GITHUB_TOKEN': 'ghs',
                  'YOUTUBE_OAUTH_JSON': '{}', 'ANTHROPIC_API_KEY': 'sk-ant'}
        env = af.claude_env('/tmp/h', source)
        self.assertEqual(env['CLAUDE_CODE_OAUTH_TOKEN'], 'sk-oat')
        self.assertEqual(env['HOME'], '/tmp/h')
        self.assertFalse({'GITHUB_TOKEN', 'YOUTUBE_OAUTH_JSON', 'ANTHROPIC_API_KEY'} & set(env))
        with self.assertRaisesRegex(RuntimeError, 'setup-token'):
            af.claude_env('/tmp/h', {'PATH': '/bin'})

    def test_author_uses_bounded_cli_and_reports_failures(self):
        result = json.dumps({'type': 'result', 'result': '{"ok":1}'})
        calls = []

        async def run(args, **kw):
            calls.append((args, kw))
            return 0, result, ''
        author = af.make_author('/bin/claude', {'HOME': '/h'}, 'off', run=run)
        self.assertEqual(asyncio.run(author('brief')), '{"ok":1}')
        args, kw = calls[0]
        self.assertIn('--tools', args)
        self.assertEqual(args[args.index('--tools') + 1], '')
        self.assertEqual(kw['stdin'], 'brief')

        async def limited(args, **kw):
            return 1, json.dumps({'type': 'result', 'is_error': True, 'result': 'Claude AI usage limit reached|1794400000'}), ''
        author = af.make_author('/bin/claude', {}, 'auto', run=limited)
        with self.assertRaisesRegex(RuntimeError, 'usage limit reached'):
            asyncio.run(author('brief'))
        self.assertEqual(author.failure, 'limit')

        async def expired(args, **kw):
            return 0, json.dumps({'type': 'result', 'is_error': True,
                                  'result': 'OAuth token has expired. Please obtain a new token.'}), ''
        author = af.make_author('/bin/claude', {}, 'auto', run=expired)
        with self.assertRaisesRegex(RuntimeError, 'Renew it'):
            asyncio.run(author('brief'))
        self.assertEqual(author.failure, 'signin')

        async def crashed(args, **kw):
            return 1, '', 'segfault'
        author = af.make_author('/bin/claude', {}, 'auto', run=crashed)
        with self.assertRaisesRegex(RuntimeError, 'usage limits'):
            asyncio.run(author('brief'))
        self.assertEqual(author.failure, '')

    def test_claude_issue_classification(self):
        self.assertEqual(af.claude_issue('API Error: 401 {"type":"authentication_error"}'), 'signin')
        self.assertEqual(af.claude_issue('Invalid bearer token'), 'signin')
        self.assertEqual(af.claude_issue("You've hit your usage limit · resets 3pm"), 'limit')
        self.assertEqual(af.claude_issue('Network unreachable'), '')

    def test_failed_review_never_approves(self):
        with tempfile.TemporaryDirectory() as root:
            sheet = Path(root) / 'review.png'
            sheet.write_bytes(b'png')

            async def failing(args, **kw):
                return 1, '', ''
            self.assertFalse(asyncio.run(af.make_review('claude', {}, run=failing)(DESIGN, sheet))['passed'])

            async def passing(args, **kw):
                self.assertIn('stream-json', args)
                return 0, json.dumps({'type': 'result', 'result': '{"passed":true,"issues":[]}'}), ''
            self.assertTrue(asyncio.run(af.make_review('claude', {}, run=passing)(DESIGN, sheet))['passed'])


class RendererTests(unittest.TestCase):
    def renderer(self, root, sandbox=''):
        (Path(root) / 'runner').mkdir()
        (Path(root) / 'runner' / 'render.mjs').write_text('')
        source = {'PATH': '/usr/bin:/bin', 'CLAUDE_CODE_OAUTH_TOKEN': 'secret', 'GITHUB_TOKEN': 'ghs',
                  'STUDIO_CHROMIUM': ''}
        return af.LocalRenderer(root, sandbox=sandbox, source=source)

    def test_generated_code_renders_without_secrets(self):
        with tempfile.TemporaryDirectory() as root:
            renderer = self.renderer(root)
            job = Path(root) / 'job'
            env = renderer.environment(job)
            self.assertEqual(env['STUDIO_RENDER_ROOT'], str(job))
            self.assertEqual(env['STUDIO_CHROMIUM'], '')
            self.assertFalse({'CLAUDE_CODE_OAUTH_TOKEN', 'GITHUB_TOKEN'} & set(env))
            args, _ = renderer.command(job, env)
            self.assertTrue(args[-1].endswith('runner/render.mjs'))

    def test_network_sandbox_wraps_render_in_isolated_namespace(self):
        with tempfile.TemporaryDirectory() as root, patch('os.geteuid', return_value=1001):
            renderer = self.renderer(root, 'netns')
            job = Path(root) / 'job'
            job.mkdir()
            args, outer = renderer.command(job, renderer.environment(job))
            self.assertEqual(args[:5], ['sudo', '-n', 'unshare', '--net', '--'])
            self.assertTrue(args[7].endswith('isolate.py'))
            self.assertEqual(outer, {'PATH': '/usr/bin:/bin'})
            inner = json.loads(Path(args[10]).read_text())
            self.assertNotIn('CLAUDE_CODE_OAUTH_TOKEN', inner)
            self.assertEqual(inner['STUDIO_RENDER_ROOT'], str(job))

    def test_missing_renderer_is_reported(self):
        with tempfile.TemporaryDirectory() as root, self.assertRaisesRegex(RuntimeError, 'not prepared'):
            af.LocalRenderer(root)


class AutopilotTests(unittest.TestCase):
    def write(self, root, **settings):
        path = Path(root) / 'settings.json'
        path.write_text(json.dumps({'enabled': True, 'domain': 'Astronomy', 'days': [0], 'timezone': 'UTC',
                                    'resources': ['a' * 24], **settings}))
        return path

    def test_schedule_respects_enabled_and_days(self):
        monday = 1731283200  # 2024-11-11 00:00 UTC, a Monday
        with tempfile.TemporaryDirectory() as root:
            history = Path(root) / 'history.json'
            self.assertIn('No autopilot', af.autopilot_request(Path(root) / 'none.json', history, True))
            self.assertIn('disabled', af.autopilot_request(self.write(root, enabled=False), history, True))
            self.assertIn('not a selected', af.autopilot_request(self.write(root), history, True, now=monday + 86400))
            brief, seconds, voice, options, automation = af.autopilot_request(self.write(root), history, True, now=monday)
            self.assertEqual((brief, seconds, voice), ('Astronomy', 30, 'auto'))
            self.assertNotIn('resources', options['autonomous'])
            self.assertEqual(options['research'], 'auto')
            self.assertEqual(automation.history(), [])
            # A manual run uses the settings even while the daily schedule is disabled.
            self.assertEqual(af.autopilot_request(self.write(root, enabled=False), history, False)[0], 'Astronomy')
            with self.assertRaisesRegex(RuntimeError, 'domain'):
                af.autopilot_request(self.write(root, domain=' '), history, False)


class CreateTests(unittest.TestCase):
    def args(self, root, *extra):
        return af.parse_args(['--renderer', root, '--out', str(Path(root) / 'out'), '--seed', 'run-1', *extra])

    def test_manual_film_is_rendered_repaired_and_marked_for_delivery(self):
        with tempfile.TemporaryDirectory() as root:
            render = fake_render(fail_first=True)
            author = fake_author(DESIGN, DESIGN)
            result = asyncio.run(af.create(self.args(root, '--brief', 'Day and night', '--publish',
                '--privacy', 'unlisted'), author=author, review=review_result(True), render=render,
                synth=fake_synth, probe=lambda f: 3.0, join=fake_join, writers=[]))
            self.assertEqual(result['state'], 'ready', result)
            self.assertEqual(render.calls, 2)
            self.assertIn('Renderer error', author.prompts[1])
            self.assertTrue(result['publish'] and result['qualityPassed'])
            self.assertEqual(result['privacy'], 'unlisted')
            self.assertEqual(result['jobId'], af.job_id_for('run-1'))
            out = Path(root) / 'out'
            for name in ('video.mp4', 'video.png', 'design.json', 'delivery.json'):
                self.assertTrue((out / name).is_file(), name)

    def test_failed_review_holds_publication(self):
        with tempfile.TemporaryDirectory() as root:
            result = asyncio.run(af.create(self.args(root, '--brief', 'x', '--publish'),
                author=fake_author(DESIGN, DESIGN), review=review_result(False), render=fake_render(),
                synth=fake_synth, probe=lambda f: 3.0, join=fake_join, writers=[]))
            self.assertEqual(result['state'], 'ready')
            self.assertTrue(result['publish'])
            self.assertFalse(result['qualityPassed'])

    def test_autopilot_researches_topic_and_records_history(self):
        with tempfile.TemporaryDirectory() as root:
            settings = Path(root) / 'settings.json'
            settings.write_text(json.dumps({'enabled': True, 'domain': 'Astronomy', 'publish': True}))
            history = Path(root) / 'history.json'
            history.write_text(json.dumps([{'jobId': 'old', 'topic': 'Eclipses'}]))
            author = fake_author(TOPIC, DESIGN, search=True)
            result = asyncio.run(af.create(self.args(root, '--autopilot', str(settings), '--history', str(history),
                '--scheduled'), author=author, review=review_result(True), render=fake_render(),
                synth=fake_synth, probe=lambda f: 3.0, join=fake_join, writers=[]))
            self.assertEqual(result['state'], 'ready', result)
            self.assertTrue(result['autonomous'] and result['publish'])
            self.assertIn('Eclipses', author.prompts[0])
            saved = json.loads((Path(root) / 'out' / 'history.json').read_text())
            self.assertEqual([x['jobId'] for x in saved], ['old', result['jobId']])
            self.assertEqual(saved[-1]['topic'], 'Leap years')

    def test_failure_is_reported_without_delivery(self):
        with tempfile.TemporaryDirectory() as root:
            result = asyncio.run(af.create(self.args(root, '--brief', 'x', '--publish', '--writer', 'claude'),
                author=fake_author({'title': 'bad'}, {'title': 'bad'}), review=review_result(True),
                render=fake_render(), synth=fake_synth, probe=lambda f: 3.0, join=fake_join, writers=[]))
            self.assertEqual(result['state'], 'failed')
            self.assertFalse(result['publish'])

    def test_invalid_inputs_are_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            for extra in (['--seconds', '5'], ['--voice', 'android'], ['--privacy', 'friends']):
                with self.assertRaises(RuntimeError):
                    asyncio.run(af.create(self.args(root, '--brief', 'x', *extra), author=fake_author(),
                        review=review_result(True), render=fake_render(), writers=[]))


class FakeContents:
    """In-memory GitHub contents API."""
    def __init__(self):
        self.files, self.calls = {}, []

    def __call__(self, method, path, data=None):
        self.calls.append((method, path))
        if method == 'GET':
            if path not in self.files:
                raise HTTPError(path, 404, 'Not Found', {}, None)
            sha, content = self.files[path]
            return {'sha': sha, 'content': base64.b64encode(content).decode()}
        if path in self.files and data.get('sha') != self.files[path][0]:
            raise HTTPError(path, 422, 'sha', {}, None)
        sha = str(len(self.calls))
        self.files[path] = (sha, base64.b64decode(data['content']))
        return {'content': {'sha': sha}}

    def json(self, path):
        return json.loads(self.files[path][1])


class DeliverTests(unittest.TestCase):
    def folder(self, root, **delivery):
        folder = Path(root)
        (folder / 'video.mp4').write_bytes(b'v')
        (folder / 'design.json').write_text(json.dumps(DESIGN))
        (folder / 'delivery.json').write_text(json.dumps({'state': 'ready', 'jobId': 'b' * 32,
            'publish': True, 'privacy': 'public', 'qualityPassed': True, 'autonomous': False, **delivery}))
        return folder

    def test_ledger_creates_once_and_updates_with_sha(self):
        api = FakeContents()
        store = LedgerStore({'token': 't'}, api, branch='main')
        self.assertEqual(store.get('youtube-token'), {'token': 't'})
        self.assertTrue(store.put('upload:abc', {'state': 'upload_started'}, skip_if_exists=True))
        store['upload:abc'] = {'state': 'posted'}
        self.assertEqual(api.json('shorts-studio/ledger/abc.json'), {'state': 'posted'})
        again = LedgerStore({}, api, branch='main')
        self.assertFalse(again.put('upload:abc', {'state': 'upload_started'}, skip_if_exists=True))
        self.assertEqual(again.get('upload:abc'), {'state': 'posted'})

    def test_upload_uses_real_youtube_receipts(self):
        from youtube import YouTube
        api = FakeContents()
        uploads = []

        class FakeYouTube(YouTube):
            def upload(self, job_id, video, design, privacy, **kw):
                # Exercise the store contract the real uploader relies on.
                key = 'upload:' + job_id
                assert self.store.put(key, {'state': 'upload_started'}, skip_if_exists=True)
                assert self.store.get('youtube-token') == {'refresh_token': 'r'}
                uploads.append((job_id, video.name, design['title'], privacy))
                receipt = {'state': 'posted', 'url': 'https://www.youtube.com/shorts/x'}
                self.store[key] = receipt
                return receipt
        with tempfile.TemporaryDirectory() as root:
            folder = self.folder(root)
            result = deliver(folder, api=api, youtube_factory=FakeYouTube,
                             source={'YOUTUBE_OAUTH_JSON': '{"refresh_token":"r"}'})
            self.assertTrue(result['uploaded'])
            self.assertEqual(uploads, [('b' * 32, 'video.mp4', 'The moving Earth', 'public')])
            self.assertEqual(api.json('shorts-studio/ledger/' + 'b' * 32 + '.json')['state'], 'posted')

    def test_held_or_unconfigured_uploads_do_not_post(self):
        api = FakeContents()
        with tempfile.TemporaryDirectory() as root:
            self.assertFalse(deliver(self.folder(root, qualityPassed=False), api=api, source={})['uploaded'])
            with self.assertRaisesRegex(RuntimeError, 'YOUTUBE_OAUTH_JSON'):
                deliver(self.folder(root), api=api, source={})
            self.assertFalse(deliver(self.folder(root, state='failed'), api=api, source={})['uploaded'])
        self.assertEqual(api.files, {})

    def test_autopilot_history_is_committed(self):
        api = FakeContents()
        api.files['shorts-studio/autopilot/history.json'] = ('s0', b'[]')
        with tempfile.TemporaryDirectory() as root:
            folder = self.folder(root, publish=False, autonomous=True)
            (folder / 'history.json').write_text(json.dumps([{'jobId': str(i)} for i in range(95)]))
            self.assertEqual(deliver(folder, api=api, source={})['history'], 95)
        saved = api.json('shorts-studio/autopilot/history.json')
        self.assertEqual(len(saved), 90)
        self.assertEqual(saved[-1]['jobId'], '94')

    def test_repository_file_reads_missing_as_default(self):
        self.assertEqual(RepositoryFile('x.json', FakeContents(), branch='').read([]), [])


if __name__ == '__main__':
    unittest.main()


class FakeReleases:
    """In-memory GitHub releases API for Library."""
    def __init__(self):
        self.releases, self.assets, self.next = [], {}, 100

    def __call__(self, method, url, body=None, headers=None):
        import re
        from urllib.parse import parse_qs, urlsplit
        path = urlsplit(url).path
        if method == 'GET' and path.endswith('/releases'):
            return 200, json.dumps(self.releases).encode(), {}
        if method == 'POST' and path.endswith('/releases'):
            release = {'id': 7, **body}
            self.releases.append(release)
            return 201, json.dumps(release).encode(), {}
        if method == 'GET' and path.endswith('/assets'):
            return 200, json.dumps([{'id': i, 'name': a['name']} for i, a in self.assets.items()]).encode(), {}
        if method == 'POST' and 'uploads.github.com' in url:
            self.next += 1
            self.assets[self.next] = {'name': parse_qs(urlsplit(url).query)['name'][0], 'data': body,
                                      'type': headers['Content-Type']}
            return 201, json.dumps({'id': self.next}).encode(), {}
        match = re.search(r'/releases/assets/(\d+)$', path)
        if match and method == 'DELETE':
            del self.assets[int(match.group(1))]
            return 204, b'', {}
        if match and method == 'GET':
            return 302, b'', {'Location': 'https://storage.example/' + match.group(1)}
        if url.startswith('https://storage.example/'):
            return 200, self.assets[int(url.rsplit('/', 1)[1])]['data'], {}
        raise AssertionError((method, url))

    def named(self, name):
        return [a for a in self.assets.values() if a['name'] == name]


class LibraryTests(unittest.TestCase):
    def test_films_are_saved_to_a_private_draft_release(self):
        from actions_deliver import Library
        api = FakeReleases()
        with tempfile.TemporaryDirectory() as root:
            folder = DeliverTests().folder(root, publish=False, title='Tides', writer='backup')
            (folder / 'video.png').write_bytes(b'png')
            deliver(folder, api=FakeContents(), source={'GITHUB_RUN_ID': '11'}, library=Library('o/r', api))
            deliver(folder, api=FakeContents(), source={'GITHUB_RUN_ID': '11'}, library=Library('o/r', api))
        self.assertEqual(len(api.releases), 1)
        self.assertTrue(api.releases[0]['draft'])
        self.assertEqual(len(api.named('film-11.mp4')), 1)
        index = json.loads(api.named('library.json')[0]['data'])
        self.assertEqual(len(index), 1)
        self.assertEqual((index[0]['run'], index[0]['title'], index[0]['writer']), ('11', 'Tides', 'backup'))
        self.assertIn(index[0]['video'], api.assets)
        self.assertEqual(api.assets[index[0]['video']]['type'], 'video/mp4')

    def test_library_keeps_the_newest_films(self):
        from actions_deliver import Library
        api = FakeReleases()
        library = Library('o/r', api)
        library.KEEP = 2
        with tempfile.TemporaryDirectory() as root:
            folder = DeliverTests().folder(root, publish=False)
            for run in ('1', '2', '3'):
                deliver(folder, api=FakeContents(), source={'GITHUB_RUN_ID': run}, library=library)
        names = sorted(a['name'] for a in api.assets.values())
        self.assertEqual(names, ['film-2.mp4', 'film-3.mp4', 'library.json'])
        self.assertEqual([f['run'] for f in json.loads(api.named('library.json')[0]['data'])], ['3', '2'])

    def test_youtube_failure_is_recorded_in_library_then_raised(self):
        from actions_deliver import Library
        api = FakeReleases()
        with tempfile.TemporaryDirectory() as root:
            with self.assertRaisesRegex(RuntimeError, 'YOUTUBE_OAUTH_JSON'):
                deliver(DeliverTests().folder(root), api=FakeContents(), source={'GITHUB_RUN_ID': '5'},
                        library=Library('o/r', api))
        self.assertIn('YOUTUBE_OAUTH_JSON', json.loads(api.named('library.json')[0]['data'])[0]['uploadError'])

    def test_rest_sends_token_only_to_github(self):
        from actions_deliver import rest
        sent = []

        class Response:
            status = 200
            headers = {}
            def read(self):
                return b'{}'
            def __enter__(self):
                return self
            def __exit__(self, *a):
                return False

        class Opener:
            def open(self, request, timeout):
                sent.append(dict(request.header_items()))
                return Response()
        with patch('actions_deliver.build_opener', return_value=Opener()):
            rest('GET', '/repos/o/r', source={'GITHUB_TOKEN': 'ghs'})
            rest('GET', 'https://storage.example/x', source={'GITHUB_TOKEN': 'ghs'})
        self.assertEqual(sent[0]['Authorization'], 'Bearer ghs')
        self.assertNotIn('Authorization', sent[1])


class PublishTests(unittest.TestCase):
    def library_with(self, api, runs):
        from actions_deliver import Library
        library = Library('o/r', api)
        with tempfile.TemporaryDirectory() as root:
            folder = DeliverTests().folder(root, publish=False)
            for run in runs:
                (folder / 'video.mp4').write_bytes(b'video-' + run.encode())
                deliver(folder, api=FakeContents(), source={'GITHUB_RUN_ID': run}, library=library)
        return library

    def test_selected_films_are_uploaded_and_recorded_in_place(self):
        from actions_publish import parse_films, publish
        api = FakeReleases()
        library = self.library_with(api, ['1', '2'])
        uploads = []

        class FakeYouTube:
            def upload(self, job, video, design, privacy, publish_at=None, hashtags=()):
                uploads.append((video.read_bytes(), design['title'], privacy, publish_at))
                return {'url': 'https://www.youtube.com/shorts/x' + video.name, 'scheduledFor': publish_at}
        films = parse_films(json.dumps([{'run': '1', 'publishAt': '2026-11-20T07:00:00Z'}, {'run': '2'}, {'run': '9'}]))
        with tempfile.TemporaryDirectory() as work:
            results = publish(films, 'public', library=library, youtube=FakeYouTube(), workdir=work)
        self.assertEqual(uploads, [(b'video-1', 'Shorts Studio film', 'public', '2026-11-20T07:00:00Z'),
                                   (b'video-2', 'Shorts Studio film', 'public', None)])
        self.assertEqual(results[2]['error'], 'Film not found in the library')
        index = json.loads(api.named('library.json')[0]['data'])
        self.assertEqual([f['run'] for f in index], ['2', '1'])  # order unchanged
        self.assertEqual(index[1]['scheduledFor'], '2026-11-20T07:00:00Z')
        self.assertTrue(index[0]['youtube'].startswith('https://www.youtube.com/shorts/'))

    def test_private_films_are_not_scheduled_and_errors_are_kept(self):
        from actions_publish import publish
        api = FakeReleases()
        library = self.library_with(api, ['3'])

        class Failing:
            def upload(self, job, video, design, privacy, publish_at=None, hashtags=()):
                assert publish_at is None
                raise RuntimeError('quota exceeded')
        with tempfile.TemporaryDirectory() as work:
            results = publish([{'run': '3', 'publishAt': '2026-11-20T07:00:00Z'}], 'private', library=library,
                              youtube=Failing(), workdir=work)
        self.assertEqual(results[0]['uploadError'], 'quota exceeded')
        self.assertEqual(json.loads(api.named('library.json')[0]['data'])[0]['uploadError'], 'quota exceeded')

    def test_selection_is_validated(self):
        from actions_publish import parse_films
        for raw in ('[]', json.dumps([{'run': 'x'}]), json.dumps([{'run': '1', 'publishAt': 'tomorrow'}]),
                    json.dumps([{'run': str(i)} for i in range(21)])):
            with self.assertRaises(ValueError):
                parse_films(raw)
