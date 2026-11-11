import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from modal_bridge import create_service


class LibraryTests(unittest.TestCase):
    def test_phone_and_web_can_recover_ready_failed_and_expired_films(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {'STUDIO_ACCESS': 'private-test'}):
            jobs = Path(root) / 'productions'
            for i, (state, available) in enumerate([('ready', True), ('failed', False), ('ready', False)]):
                job_id = str(i + 1) * 32
                folder = jobs / job_id
                folder.mkdir(parents=True)
                (folder / 'status.json').write_text(json.dumps({
                    'id': job_id, 'state': state, 'title': 'Film ' + str(i),
                    'message': 'Ready' if state == 'ready' else 'Creation failed', 'createdAt': i + 1}))
                if available:
                    (folder / 'video.mp4').write_bytes(b'retained-video')
            # A malformed file or folder is never returned as a usable film.
            invalid = jobs / 'invalid'
            invalid.mkdir()
            (invalid / 'status.json').write_text('{}')
            with TestClient(create_service(root, render_image=object())) as client:
                self.assertEqual(client.get('/productions').status_code, 401)
                response = client.get('/productions', headers={'Authorization': 'Bearer private-test'})
                self.assertEqual(response.status_code, 200, response.text)
                rows = response.json()['productions']
                self.assertEqual([r['id'] for r in rows], ['3' * 32, '2' * 32, '1' * 32])
                self.assertEqual([r['available'] for r in rows], [False, False, True])
                video = client.get('/productions/' + '1' * 32 + '/video',
                                   headers={'Authorization': 'Bearer private-test'})
                self.assertEqual(video.content, b'retained-video')

    def test_queued_cloud_job_appears_before_its_volume_folder_exists(self):
        from fastapi import FastAPI
        from studio import install_routes
        from autonomy import FileStore
        from unittest.mock import Mock, AsyncMock
        with tempfile.TemporaryDirectory() as root:
            production = Mock()
            production.root = Path(root) / 'productions'
            production.jobs = {'active': 'a' * 32}
            production.prepare_download = AsyncMock()
            production.status.return_value = {'id': 'a' * 32, 'state': 'queued', 'createdAt': 1}
            app = FastAPI()
            install_routes(app, root, lambda: None, production, FileStore(root), lambda: None, lambda: None)
            with TestClient(app) as client:
                rows = client.get('/productions').json()['productions']
                self.assertEqual(rows, [{'id': 'a' * 32, 'state': 'queued', 'createdAt': 1, 'available': False}])
            production.prepare_download.assert_awaited_once()

    def test_sign_in_has_a_dedicated_setup_route(self):
        with tempfile.TemporaryDirectory() as root:
            with TestClient(create_service(root)) as client:
                response = client.get('/setup')
                self.assertEqual(response.status_code, 200)
                self.assertIn('Start official Claude login', response.text)


if __name__ == '__main__':
    unittest.main()
