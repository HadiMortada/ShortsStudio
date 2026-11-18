import io
import json
import tempfile
import unittest
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from PIL import Image

import resources
from production import parse_design


def png(width=1200, height=900):
    out = io.BytesIO()
    Image.new('RGB', (width, height), (30, 60, 90)).save(out, 'JPEG')
    return out.getvalue()


def commons_page(title, license_name='CC BY-SA 4.0', width=3000, height=2000, index=1, mime='image/jpeg'):
    return {'title': title, 'index': index, 'imageinfo': [{
        'url': 'https://upload.wikimedia.org/full/' + title, 'thumburl': 'https://upload.wikimedia.org/thumb/' + title,
        'descriptionurl': 'https://commons.wikimedia.org/wiki/' + title, 'mime': mime, 'width': width, 'height': height,
        'extmetadata': {'LicenseShortName': {'value': license_name}, 'Artist': {'value': '<a>Jane Photographer</a>'},
                        'LicenseUrl': {'value': 'https://creativecommons.org/licenses/by-sa/4.0'}}}]}


class Archive:
    """Answers like Wikimedia Commons and the NASA Image Library."""

    def __init__(self, commons=(), nasa=(), exact=None):
        self.commons, self.nasa, self.exact, self.urls = list(commons), list(nasa), exact, []

    def __call__(self, url, limit):
        self.urls.append(url)
        resources.check_media_url(url)
        parts = urlsplit(url)
        query = parse_qs(parts.query)
        if parts.hostname == 'commons.wikimedia.org':
            if 'titles' in query:
                pages = {'1': self.exact} if self.exact else {'-1': {'missing': ''}}
            else:
                pages = {str(i): p for i, p in enumerate(self.commons)}
            return json.dumps({'query': {'pages': pages}}).encode()
        if parts.hostname == 'images-api.nasa.gov':
            return json.dumps({'collection': {'items': [{'data': [x]} for x in self.nasa]}}).encode()
        return png()


class MediaTests(unittest.TestCase):
    def fetch(self, request, archive):
        with tempfile.TemporaryDirectory() as root:
            destination = Path(root) / 'photo.png'
            credit = resources.fetch_media(request, destination, archive)
            self.assertTrue(destination.is_file())
            return credit

    def test_search_finds_a_licensed_photo_and_keeps_its_credit(self):
        archive = Archive(commons=[commons_page('File:Small.jpg', width=300, height=200, index=1),
                                   commons_page('File:Rights reserved.jpg', license_name='All rights reserved', index=2),
                                   commons_page('File:Aldrin bootprint.jpg', license_name='Public domain', index=3)])
        credit = self.fetch({'id': 'boot', 'kind': 'image', 'query': 'Buzz Aldrin bootprint'}, archive)
        self.assertEqual(credit['title'], 'File:Aldrin bootprint.jpg')
        self.assertIn('Jane Photographer', credit['credit'])
        self.assertIn('Public domain', credit['credit'])
        self.assertIn('filetype:bitmap', parse_qs(urlsplit(archive.urls[0]).query)['gsrsearch'][0])
        # The 2000 px copy is downloaded, not the full original.
        self.assertTrue(archive.urls[-1].startswith('https://upload.wikimedia.org/thumb/'))

    def test_a_wrong_exact_title_falls_back_to_searching(self):
        archive = Archive(commons=[commons_page('File:Hagia Sophia dome.jpg')])
        credit = self.fetch({'id': 'dome', 'kind': 'image', 'title': 'File:Hagia_Sophia_interior (2019).jpg'}, archive)
        self.assertEqual(credit['title'], 'File:Hagia Sophia dome.jpg')
        searched = parse_qs(urlsplit(archive.urls[1]).query)['gsrsearch'][0]
        self.assertTrue(searched.startswith('Hagia Sophia interior'))

    def test_nasa_photos_are_used_and_third_party_ones_skipped(self):
        archive = Archive(nasa=[{'nasa_id': 'agency_ap', 'title': 'Crowd', 'description': 'AP Photo/John Doe'},
                                {'nasa_id': 'as11-40-5878', 'title': 'Bootprint', 'center': 'JSC'}])
        credit = self.fetch({'id': 'boot', 'kind': 'image', 'source': 'nasa', 'query': 'Apollo 11 bootprint'}, archive)
        self.assertEqual(credit['url'], 'https://images.nasa.gov/details/as11-40-5878')
        self.assertTrue(credit['credit'].startswith('NASA / JSC'))
        self.assertFalse(any('agency_ap' in u for u in archive.urls))
        # Commons has nothing: a space photo is still found at NASA.
        credit = self.fetch({'id': 'boot', 'kind': 'image', 'query': 'Apollo 11 bootprint'}, archive)
        self.assertEqual(credit['source'], 'NASA')

    def test_nothing_licensed_means_a_clear_failure(self):
        archive = Archive(commons=[commons_page('File:X.jpg', license_name='Fair use')])
        with self.assertRaisesRegex(ValueError, 'No NASA image|license'):
            self.fetch({'id': 'x', 'kind': 'image', 'query': 'something'}, archive)

    def test_only_the_two_archives_can_be_downloaded(self):
        for url in ('https://example.com/a.jpg', 'http://upload.wikimedia.org/a.jpg', 'https://images-assets.nasa.gov.evil.com/a'):
            with self.assertRaises(ValueError):
                resources.check_media_url(url)

    def test_writer_assets_take_a_query_or_a_title(self):
        from test_production import design
        film = {**design(), 'assets': [{'id': 'boot', 'kind': 'image', 'source': 'nasa', 'query': 'Apollo 11 bootprint'}]}
        self.assertEqual(parse_design(json.dumps(film))['assets'][0]['source'], 'nasa')
        with self.assertRaises(Exception):
            parse_design(json.dumps({**design(), 'assets': [{'id': 'x', 'kind': 'image'}]}))


if __name__ == '__main__':
    unittest.main()


class LiveShapeTests(unittest.TestCase):
    """Shapes from a real Commons answer (18 Nov 2026) for "ancient leather sandal museum"."""

    def test_real_commons_answer_gives_a_cc0_photo_from_the_thumbnail_host(self):
        pages = {
            '16688295': {'title': 'File:1178 - Keramikos Museum.jpg', 'index': 1, 'imageinfo': [{
                'width': 2804, 'height': 1492, 'mime': 'image/jpeg',
                'thumburl': 'https://upload.wikimedia.org/wikipedia/commons/f/f0/1178.jpg?utm_source=commons.wikimedia.org',
                'extmetadata': {'LicenseShortName': {'value': 'Attribution'}}}]},  # not a license we can verify
            '38328980': {'title': 'File:Leather sandal of a child, Roman period.jpg', 'index': 2, 'imageinfo': [{
                'width': 4509, 'height': 2784, 'mime': 'image/jpeg',
                'thumburl': 'https://thumb.wikimedia.org/wikipedia/commons/thumb/6/6a/L.jpg/3840px-L.jpg?utm_source=commons.wikimedia.org',
                'descriptionurl': 'https://commons.wikimedia.org/wiki/File:L.jpg',
                'extmetadata': {'LicenseShortName': {'value': 'CC BY-SA 2.0'}, 'Artist': {'value': 'Carole Raddato'},
                                'LicenseUrl': {'value': 'https://creativecommons.org/licenses/by-sa/2.0'}}}]}}

        def fetch(url, limit):
            resources.check_media_url(url)
            return json.dumps({'query': {'pages': pages}}).encode() if 'api.php' in url else png()
        with tempfile.TemporaryDirectory() as root:
            credit = resources.fetch_media({'id': 'sandal', 'kind': 'image', 'query': 'ancient leather sandal museum'},
                                           Path(root) / 'sandal.png', fetch)
        self.assertEqual(credit['title'], 'File:Leather sandal of a child, Roman period.jpg')
        self.assertIn('Carole Raddato', credit['credit'])

    def test_every_reason_is_kept_when_nothing_works(self):
        def refused(url, limit):
            raise OSError('HTTP Error 403: Forbidden')
        with tempfile.TemporaryDirectory() as root, self.assertRaises(ValueError) as caught:
            resources.fetch_media({'id': 'x', 'kind': 'image', 'query': 'Dhi Qar landscape'}, Path(root) / 'x.png', refused)
        self.assertEqual(str(caught.exception).count('403'), 2)  # Commons and NASA both explained

    def test_downloads_name_the_app_and_its_repository(self):
        self.assertEqual(resources.user_agent({'GITHUB_REPOSITORY': 'me/studio'}),
                         'ShortsStudio/0.8 (https://github.com/me/studio; licensed media with credits)')
        self.assertNotIn('github', resources.user_agent({}))
