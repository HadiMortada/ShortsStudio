import json
import shutil
import unittest
from unittest.mock import patch

from PIL import Image

import edit_graphics
import edit_references
import edit_sound
import video_edit


def sample_pdf():
    stream = b'BT /F1 18 Tf 36 550 Td (Gravity pulls objects toward Earth.) Tj ET'
    objects = [
        b'<< /Type /Catalog /Pages 2 0 R >>',
        b'<< /Type /Pages /Kids [3 0 R] /Count 1 >>',
        b'<< /Type /Page /Parent 2 0 R /MediaBox [0 0 420 600] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>',
        b'<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>',
        b'<< /Length %d >>\nstream\n' % len(stream) + stream + b'\nendstream',
    ]
    data, offsets = b'%PDF-1.4\n', []
    for i, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data += b'%d 0 obj\n' % i + obj + b'\nendobj\n'
    xref = len(data)
    data += b'xref\n0 6\n0000000000 65535 f \n'
    data += b''.join(b'%010d 00000 n \n' % offset for offset in offsets)
    return data + b'trailer\n<< /Root 1 0 R /Size 6 >>\nstartxref\n%d\n%%%%EOF\n' % xref


class ReferenceTests(unittest.TestCase):
    def test_arabic_words_match_a_complete_passage_in_reading_order(self):
        lines = [[('عَلى', (.25, .2, .4, .25)), ('توكلتُ', (.4, .2, .6, .25)), ('أنا', (.6, .2, .75, .25))],
                 [('الله', (.55, .3, .75, .35))]]
        self.assertEqual(edit_references.find_boxes(lines, 'أنا توكلت على الله'),
                         [(.25, .2, .75, .25), (.55, .3, .75, .35)])
        self.assertEqual(edit_references.find_boxes(lines, 'أنا توكلت على نفسي'), [])

    def test_download_refuses_non_public_addresses(self):
        with patch('edit_references.socket.getaddrinfo', return_value=[(2, 1, 6, '', ('127.0.0.1', 443))]):
            with self.assertRaisesRegex(ValueError, 'outside the public web'):
                edit_references.public_document('https://source.example/paper.pdf')

    @unittest.skipUnless(shutil.which('pdftoppm') and shutil.which('pdftotext'), 'needs Poppler')
    def test_research_only_shows_a_pdf_with_the_exact_passage(self):
        pdf = sample_pdf()
        quotes = [{'ref': '1:6', 'text': 'Gravity pulls objects toward Earth', 'source': 'Science Notes'},
                  {'ref': '9:13', 'text': 'Gravity pushes rocks away', 'source': 'Science Notes'}]
        url = 'https://publisher.example/science.pdf'
        def research(prompt):
            self.assertIn('Gravity pulls objects toward Earth', prompt)
            return json.dumps({'sources': [{'ref': v['ref'], 'url': url, 'page': 1} for v in quotes]})
        pages, records = edit_references.find_pages(quotes, research, json.loads, 'en', fetch=lambda _: pdf)
        self.assertEqual(set(pages), {'1:6'})
        self.assertEqual(records['1:6']['page'], 1)
        image, boxes = pages['1:6']
        self.assertAlmostEqual(image.width / image.height, .7, places=2)
        self.assertTrue(all(0 < part < 1 for part in boxes[0]))
        reference = {'type': 'reference', 'start': 0, 'end': 3, 'ref': '1:6', 'citation': 'Science Notes · PDF p. 1'}
        [visual] = edit_graphics.layout([reference], (270, 480), 'en', edit_graphics.theme('gold'), references=pages)
        frames = [frame for frame, _, _ in edit_graphics.frames(visual, image, 5, (270, 480))]
        self.assertEqual(frames[6].size, (270, 480))
        self.assertNotEqual(frames[1].tobytes(), frames[6].tobytes())

    @unittest.skipUnless(shutil.which('pdftoppm') and shutil.which('pdftotext'), 'needs Poppler')
    def test_article_print_view_matches_real_arabic_text(self):
        source = ('<!doctype html><html><meta charset="UTF-8"><title>تقريب المعنى</title>'
                  '<article><h1>تقريب المعنى</h1><h2>فقرة</h2>'
                  '<p>نص قصير قبل موضع الاستشهاد في الصفحة.</p>'
                  '<p>يُحافظُ المرءُ على وقتِه ويُنظّمُ أولويّاتِه.</p>'
                  '<p>نص بعد موضع الاستشهاد في الصفحة.</p></article></html>').encode()
        quote = {'ref': '3:7', 'text': 'يحافظ المرء على وقته', 'source': 'تقريب المعنى'}
        url = 'https://publisher.example/article'
        pages, refs = edit_references.find_pages([quote], None, json.loads, 'ar',
                                                previous=[{'ref': '3:7', 'url': url}], fetch=lambda _: source)
        self.assertIn('3:7', pages)
        self.assertEqual(refs['3:7']['medium'], 'article')
        self.assertGreater(len(pages['3:7'][1]), 0)

    def test_wide_photo_keeps_both_edges_in_portrait(self):
        image = Image.new('RGB', (800, 250), 'red')
        image.paste('blue', (500, 0, 800, 250))
        photo = {'type': 'picture', 'start': 0, 'end': 2, 'id': 1}
        [visual] = edit_graphics.layout([photo], (270, 480), 'en', edit_graphics.theme('gold'), {1: image})
        frame = list(edit_graphics.frames(visual, image, 3, (270, 480)))[2][0]
        self.assertEqual(frame.getpixel((25, 211))[:3], (255, 0, 0))
        self.assertEqual(frame.getpixel((242, 211))[:3], (0, 0, 255))

    def test_motion_steps_use_their_spoken_timestamps_and_only_chosen_cues_play(self):
        visual = video_edit.parse_visuals([{'type': 'motion', 'layout': 'sequence', 'from': 0, 'to': 5,
                                            'text': 'Water cycle', 'sound': 'accent',
                                            'items': [{'at': 0, 'text': 'Evaporation'}, {'at': 3, 'text': 'Condensation'}]}], 6)[0]
        [scheduled] = video_edit.schedule_visuals([visual], '', {n: (n / 2, n / 2 + .4) for n in range(6)}, 4)
        self.assertEqual([item['reveal'] for item in scheduled['items']], [0, 1.5])
        [motion] = edit_graphics.layout([scheduled], (270, 480), 'en', edit_graphics.theme('gold'))
        frames = [frame for frame, _, _ in edit_graphics.frames(motion, None, 5, (270, 480))]
        self.assertNotEqual(frames[5].tobytes(), frames[11].tobytes())
        ref = {'type': 'reference', 'from': 6, 'to': 9}
        scene, page = video_edit.schedule_visuals([visual, ref], '', {n: (n / 2, n / 2 + .4) for n in range(10)}, 6)
        self.assertEqual((scene['type'], page['type']), ('motion', 'reference'))
        self.assertLess(scene['end'], page['start'])
        self.assertEqual(edit_sound.events_for([{'type': 'reference', 'start': 0, 'end': 2},
                                                {**scheduled, 'start': 1},
                                                {'type': 'picture', 'start': 2.2, 'end': 3},
                                                {**scheduled, 'start': 3}]), [(3, 'accent')])
