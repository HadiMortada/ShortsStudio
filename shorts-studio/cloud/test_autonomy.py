import asyncio
import io
import json
import tempfile
import unittest
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, Mock, patch

from agent import cli_args, unpack_output
from autonomy import Automation, FileStore
from production import ProductionManager
from resources import ResourceStore, fetch_commons
from test_production import design
from youtube import YouTube


class AgentTests(unittest.TestCase):
    def test_research_is_available_but_shell_and_credential_files_are_not(self):
        args = cli_args('claude')
        self.assertEqual(args[args.index('--tools')+1], 'WebSearch,WebFetch')
        self.assertNotIn('Read', args)
        self.assertNotIn('Bash', args)
        self.assertNotIn('--dangerously-skip-permissions', args)
        self.assertEqual(cli_args('claude', 'off')[args.index('--tools')+1], '')

    def test_only_final_result_is_parsed_and_search_use_is_audited(self):
        lines = [ {'type': 'assistant', 'message': {'content': [
            {'type': 'tool_use', 'name': 'WebSearch', 'input': {'query': 'question'}}]}},
            {'type': 'result', 'result': '{"title":"A film"}', 'is_error': False}]
        result, audit = unpack_output('\n'.join(map(json.dumps, lines)))
        self.assertEqual(json.loads(result)['title'], 'A film')
        self.assertTrue(audit['searchUsed'])
        with self.assertRaises(RuntimeError):
            unpack_output('{"type":"assistant","message":{"content":[]}}')


class ResourceTests(unittest.TestCase):
    def test_folder_text_and_images_are_supplied_without_credential_access(self):
        from PIL import Image
        data = io.BytesIO()
        Image.new('RGB', (30, 40), 'blue').save(data, 'JPEG')
        with tempfile.TemporaryDirectory() as root:
            store = ResourceStore(root)
            folder = store.ingest([('books/passage.txt', 'نص الحديث كما ورد'.encode()),
                                   ('pictures/sky.jpg', data.getvalue())], rights=True)
            context, credits = store.context([folder['id']], Path(root) / 'assets')
            self.assertEqual(context[0]['text'], 'نص الحديث كما ورد')
            self.assertTrue((Path(root) / context[1]['asset']).is_file())
            self.assertEqual(len(credits), 2)
            self.assertNotIn('text', folder['files'][0])

    def test_zip_traversal_and_symlinks_are_rejected(self):
        for name, mode in [('../outside.txt', 0), ('link.txt', 0o120777 << 16)]:
            raw = io.BytesIO()
            with zipfile.ZipFile(raw, 'w') as archive:
                info = zipfile.ZipInfo(name)
                info.external_attr = mode
                archive.writestr(info, 'unsafe')
            with tempfile.TemporaryDirectory() as root:
                with self.assertRaises(ValueError):
                    ResourceStore(root).ingest([('folder.zip', raw.getvalue())], rights=True)
                self.assertEqual(list((Path(root) / 'resources').iterdir()), [])

    def test_commons_requires_a_verified_license_before_downloading_media(self):
        response = {'query': {'pages': {'1': {'imageinfo': [{'url': 'https://upload.wikimedia.org/test.png',
            'mime': 'image/png', 'extmetadata': {'LicenseShortName': {'value': 'All rights reserved'}}}]}}}}
        fetch = Mock(return_value=json.dumps(response).encode())
        with self.assertRaisesRegex(ValueError, 'license'):
            fetch_commons({'title': 'File:Sky.png', 'kind': 'image'}, Path('/tmp/unused.png'), fetch)
        self.assertEqual(fetch.call_count, 1)

    def test_usage_rights_are_explicit_and_unsupported_folders_fail_cleanly(self):
        with tempfile.TemporaryDirectory() as root:
            store = ResourceStore(root)
            with self.assertRaisesRegex(ValueError, 'Confirm'):
                store.ingest([('text.txt', b'hello')])
            with self.assertRaisesRegex(ValueError, 'No usable files'):
                store.ingest([('program.exe', b'no')], rights=True)


class ScheduleTests(unittest.TestCase):
    def test_cloud_slot_runs_once_and_remains_claimed_after_settings_change(self):
        moment = datetime(2026, 11, 11, 16, 1, tzinfo=timezone.utc).timestamp()
        production = Mock(active=False)
        production.start.return_value = {'id': 'a'*32}
        with tempfile.TemporaryDirectory() as root:
            engine = Automation(FileStore(root))
            config = {'enabled': True, 'domain': 'Space', 'timezone': 'Asia/Beirut', 'time': '18:00'}
            engine.save(config, now=moment-1000)
            self.assertEqual(engine.tick(production, moment)['state'], 'queued')
            engine.tick(production, moment+100)
            engine.save({**config, 'time': '18:05'}, now=moment+101)
            engine.tick(production, moment+500)
            self.assertEqual(production.start.call_count, 1)
            options = production.start.call_args.kwargs['options']
            self.assertEqual(options['autonomous']['domain'], 'Space')
            self.assertFalse(options['publish'])

    def test_busy_and_pre_activation_slots_do_not_get_claimed(self):
        moment = datetime(2026, 11, 11, 16, 1, tzinfo=timezone.utc).timestamp()
        production = Mock(active=True)
        with tempfile.TemporaryDirectory() as root:
            engine = Automation(FileStore(root))
            engine.save({'enabled': True, 'domain': 'Space'}, now=moment-1000)
            self.assertEqual(engine.tick(production, moment)['state'], 'busy')
            production.active = False
            production.start.return_value = {'id': 'a'*32}
            self.assertEqual(engine.tick(production, moment)['state'], 'queued')
        with tempfile.TemporaryDirectory() as root:
            engine = Automation(FileStore(root))
            engine.save({'enabled': True, 'domain': 'Space'}, now=moment)
            self.assertEqual(engine.tick(production, moment+100)['state'], 'waiting')

    def test_paused_schedules_and_invalid_timezones_cannot_dispatch(self):
        with tempfile.TemporaryDirectory() as root:
            engine = Automation(FileStore(root))
            production = Mock()
            self.assertEqual(engine.tick(production)['state'], 'disabled')
            production.start.assert_not_called()
            with self.assertRaises(ValueError):
                engine.save({'enabled': True, 'domain': 'Space', 'timezone': 'Mars/Olympus'})


class PipelineTests(unittest.TestCase):
    def test_autonomous_selection_uses_search_and_passes_evidence_into_design(self):
        topic = {'topic': 'Why sunsets change color', 'angle': 'Atmosphere', 'reason': 'A recurring question',
                 'evidence': [{'title': 'Reference', 'url': 'https://example.org/reference', 'signal': 'Question'}],
                 'brief': 'Explain sunset colors in English with animated light rays'}
        async def scenario(root, searched):
            calls = []
            async def author(prompt):
                calls.append(prompt)
                author.audit = {'searchUsed': searched, 'toolCalls': 1 if searched else 0}
                return json.dumps(topic if len(calls) == 1 else design())
            async def synth(_, __, path):
                path.write_bytes(b'audio')
            def render(_, __, ___, ____, output):
                output.write_bytes(b'video')
            engine = Automation(FileStore(root))
            manager = ProductionManager(root, author, None, synth=synth, render=render,
                probe=lambda _: 12, automation=engine)
            job = manager.start('Astronomy', 15, 'auto', options={'autonomous': {'domain': 'Astronomy'}})
            await manager.task
            if searched:
                self.assertEqual(manager.status(job['id'])['state'], 'ready')
                self.assertIn('VERIFIED TOPIC RESEARCH', calls[1])
                self.assertEqual(engine.history()[0]['topic'], topic['topic'])
                self.assertIn('https://example.org/reference', (manager.root/job['id']/'sources.json').read_text())
            else:
                self.assertEqual(manager.status(job['id'])['state'], 'failed')
                self.assertEqual(len(calls), 1)
        for searched in [True, False]:
            with tempfile.TemporaryDirectory() as root:
                asyncio.run(scenario(root, searched))

    def test_failed_visual_review_holds_upload_but_keeps_finished_film(self):
        async def scenario(root):
            async def author(_):
                return json.dumps(design())
            async def synth(_, __, path):
                path.write_bytes(b'audio')
            def render(_, __, ___, ____, output):
                output.write_bytes(b'video')
            review = AsyncMock(return_value={'passed': False, 'issues': ['Clipped headline'], 'method': 'sampled'})
            youtube = Mock()
            manager = ProductionManager(root, author, None, synth=synth, render=render, probe=lambda _:10,
                                        review=review, youtube=youtube)
            job = manager.start('Earth', 15, 'auto', options={'publish': True})
            await manager.task
            self.assertEqual(manager.status(job['id'])['state'], 'ready')
            self.assertIn('Visual review', manager.status(job['id'])['uploadError'])
            youtube.upload.assert_not_called()
            self.assertEqual(review.call_count, 2)
        with tempfile.TemporaryDirectory() as root:
            asyncio.run(scenario(root))


class YouTubeTests(unittest.TestCase):
    def test_client_endpoints_are_fixed_and_invalid_callbacks_rejected(self):
        with tempfile.TemporaryDirectory() as root:
            store = FileStore(root)
            yt = YouTube(store)
            config = {'web': {'client_id': 'client.apps.googleusercontent.com', 'client_secret': 'private-test',
                'auth_uri': 'https://attacker.invalid', 'token_uri': 'https://attacker.invalid',
                'redirect_uris': ['https://example.modal.run/youtube/callback']}}
            yt.configure(config)
            self.assertEqual(store.get('youtube-client')['web']['token_uri'], 'https://oauth2.googleapis.com/token')
            self.assertNotIn('private-test', json.dumps(yt.status()))
            config['web']['redirect_uris'] = ['https://attacker.invalid/callback']
            with self.assertRaises(ValueError):
                yt.configure(config)
            with self.assertRaises(ValueError):
                yt.complete('bad-state', 'test-code')

    def test_uncertain_upload_is_never_repeated_automatically(self):
        with tempfile.TemporaryDirectory() as root:
            store = FileStore(root)
            store['youtube-token'] = {'test': 'token'}
            yt = YouTube(store)
            credential = Mock(valid=True, expired=False)
            api = Mock()
            request = api.videos.return_value.insert.return_value
            request.next_chunk.side_effect = OSError('interrupted')
            with patch('google.oauth2.credentials.Credentials.from_authorized_user_info', return_value=credential), \
                 patch('googleapiclient.discovery.build', return_value=api), \
                 patch('googleapiclient.http.MediaFileUpload'):
                for _ in range(2):
                    with self.assertRaises(RuntimeError):
                        yt.upload('a'*32, Path('/tmp/fixture.mp4'), design(), 'private')
            self.assertEqual(request.next_chunk.call_count, 1)
            self.assertEqual(store.get('upload:'+'a'*32)['state'], 'unknown')


class ConsoleTests(unittest.TestCase):
    def test_new_apis_require_pairing_and_uploads_feed_real_reference_storage(self):
        import os
        from fastapi.testclient import TestClient
        from modal_bridge import create_service
        async def run(*_, **__):
            return 0, '{"loggedIn":true}', ''
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'STUDIO_ACCESS': 'private-test'}):
            with TestClient(create_service(root, run=run, render_image=object())) as client:
                h = {'Authorization': 'Bearer private-test'}
                self.assertEqual(client.get('/studio').status_code, 200)
                for route in ['/settings', '/productions/'+'a'*32+'/sources']:
                    self.assertEqual(client.get(route).status_code, 401)
                self.assertEqual(client.put('/schedule', json={'enabled': True}).status_code, 401)
                result = client.post('/resources', headers=h, files=[('files', ('book.txt', b'Exact passage'))], data={'rights':'true'})
                self.assertEqual(result.status_code, 200, result.text)
                key = result.json()['id']
                settings = client.get('/settings', headers=h).json()
                self.assertEqual(settings['resources'][0]['id'], key)
                body = {'enabled': True, 'domain': 'Wisdom', 'resources': [key]}
                self.assertEqual(client.put('/schedule', headers=h, json=body).status_code, 200)
                self.assertEqual(client.put('/schedule', headers=h, json={**body, 'publish':True}).status_code, 422)
                self.assertEqual(client.get('/youtube/callback?state=bad&code=bad').status_code, 400)


if __name__ == '__main__':
    unittest.main()
