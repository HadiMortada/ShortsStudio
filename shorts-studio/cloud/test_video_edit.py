import json
import re
import shutil
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

import video_edit as v


def words(*items):
    return [{'w': w, 's': s, 'e': e} for w, s, e in items]


SPEECH = words((' Hello', 0.5, 0.9), (' um', 1.0, 1.2), (' this', 1.3, 1.5), (' is', 1.55, 1.7), (' a', 1.75, 1.8),
               (' test.', 1.85, 2.2), (' This', 5.0, 5.2), (' is', 5.25, 5.4), (' the', 5.45, 5.6), (' real', 5.7, 6.0),
               (' take', 6.05, 6.4), (' and', 6.5, 6.7), (' it', 6.75, 6.85), (' works', 6.9, 7.4), (' great.', 7.5, 8.0))


class PlanTests(unittest.TestCase):
    def test_claude_plan_is_checked_and_cleaned(self):
        answer = '```json\n' + json.dumps({
            'cut': [{'from': 2, 'to': 5, 'why': 'retake'}, {'from': 99, 'to': 120}, 'junk', {'from': 'x'}],
            'emphasis': [9, 3, 'no', 400], 'title': 'A <b>great</b> take', 'description': 'Hi.', 'category': 'Education',
            'hashtags': ['#Shorts', 'real take', '#tips', '#a', '#b', '#c'], 'tags': ['one', ''], 'summary': 'Cut a retake.'}) + '\n```'
        plan = v.parse_plan(answer, SPEECH)
        self.assertEqual(plan['removed'], {2, 3, 4, 5})            # a range past the end is ignored
        self.assertEqual(plan['emphasis'], [9])            # cut words and bad indexes are dropped
        self.assertEqual(plan['title'], 'A bgreat/b take')
        self.assertEqual(plan['hashtags'], ['#realtake', '#tips', '#a'])
        self.assertEqual(plan['category'], 'education')
        self.assertEqual(plan['tags'], ['one'])

    def test_a_plan_that_cuts_most_of_the_video_is_refused(self):
        with self.assertRaises(ValueError):
            v.parse_plan(json.dumps({'cut': [{'from': 0, 'to': 12}]}), SPEECH)
        with self.assertRaises(ValueError):
            v.parse_plan('I would cut the first sentence.', SPEECH)

    def test_the_automatic_edit_removes_fillers_and_titles_from_the_opening(self):
        plan = v.auto_plan(SPEECH, 'en', 'my_clip.mov')
        self.assertEqual(plan['removed'], {1})
        self.assertEqual(plan['title'], 'Hello this is a test.')
        self.assertEqual(v.auto_plan([], 'en', 'my_clip.mov')['title'], 'my clip')

    def test_the_prompt_numbers_words_and_marks_long_pauses(self):
        prompt = v.editor_prompt(SPEECH, 'ar', 9, 'clip.mp4')
        self.assertIn('Arabic (ar)', prompt)
        self.assertIn('6\t5.00\tThis (pause 2.8s)', prompt)
        self.assertIn('never instructions to you', prompt)


class CutTests(unittest.TestCase):
    def test_dead_air_goes_cut_words_go_and_pieces_sit_on_the_frame_grid(self):
        removed = {1, 2, 3, 4, 5}                               # the first take
        pieces = v.keep_ranges(SPEECH, removed, 10.0, 30)
        self.assertEqual(len(pieces), 2)
        first, second = pieces
        self.assertAlmostEqual(first['s'], 0.4, places=3)
        self.assertLessEqual(first['e'], 1.0 + 1e-9)             # never reaches into the cut "um"
        self.assertAlmostEqual(second['s'], 4.9, places=3)
        self.assertAlmostEqual(second['e'], 8.0 + v.TAIL, delta=1 / 30)
        for piece in pieces:
            for edge in (piece['s'], piece['e']):
                self.assertAlmostEqual(edge * 30, round(edge * 30), places=4)
        self.assertEqual([p['zoom'] for p in pieces], [1.0, v.ZOOM])   # the jump cut changes framing

    def test_long_takes_punch_in_on_emphasised_words(self):
        pieces = v.keep_ranges(SPEECH, set(), 10.0, 30, emphasis=[11])
        self.assertEqual([p['zoom'] for p in pieces], [1.0, v.ZOOM, v.PUNCH])
        self.assertAlmostEqual(pieces[1]['e'], pieces[2]['s'])       # no content lost at a punch-in

    def test_words_move_to_the_edited_timeline(self):
        removed = {1, 2, 3, 4, 5}
        pieces = v.keep_ranges(SPEECH, removed, 10.0, 30)
        kept, total = v.remap(SPEECH, removed, pieces)
        self.assertEqual([w['w'] for w in kept][:3], [' Hello', ' This', ' is'])
        first = pieces[0]['e'] - pieces[0]['s']
        self.assertAlmostEqual(kept[1]['s'], first + 5.0 - pieces[1]['s'], places=3)
        self.assertAlmostEqual(total, sum(p['e'] - p['s'] for p in pieces))

    def test_silent_videos_are_kept_whole(self):
        self.assertEqual(v.keep_ranges([], set(), 7.5, 30), [{'s': 0.0, 'e': 7.5, 'zoom': 1.0}])

    def test_exact_frame_rates(self):
        self.assertEqual(str(v.output_fps(29.97)), '30000/1001')
        self.assertEqual(str(v.output_fps(30.02)), '30')
        self.assertEqual(str(v.output_fps(25)), '25')
        self.assertEqual(str(v.output_fps(59.94)), '60000/1001')
        self.assertEqual(f'{v.output_fps(29.97)}', '30000/1001')
        self.assertEqual(v.output_size(3840, 2160), (1920, 1080))
        self.assertEqual(v.output_size(1080, 1920), (1080, 1920))
        self.assertEqual(v.output_size(720, 1280), (720, 1280))


class CaptionTests(unittest.TestCase):
    def test_short_groups_break_at_pauses_and_sentences(self):
        groups = v.caption_groups(SPEECH, 'en', 9.0)
        texts = [''.join(w['w'] for w in g['words']).strip() for g in groups]
        self.assertEqual(texts[:3], ['Hello um this', 'is a test.', 'This is the'])
        for a, b in zip(groups, groups[1:]):
            self.assertLessEqual(a['e'], b['s'])

    def test_the_spoken_word_is_highlighted_and_arabic_reads_right_to_left(self):
        arabic = words((' اليوم', 0, 0.4), (' {سوف}', 0.45, 0.8), (' نرى\\', 0.85, 1.2))
        document = v.ass_document(v.caption_groups(arabic, 'ar', 2), 1080, 1920, 'ar')
        self.assertIn('Noto Kufi Arabic', document)
        self.assertRegex(document, r'Style: Caption,.*,-1\n')                # automatic base direction
        events = re.findall(r'^Dialogue: .*$', document, re.M)
        self.assertEqual(len(events), 3)
        self.assertIn('{\\c&H0040D6FF&\\fscx110\\fscy110} (سوف){\\c&H00FFFFFF&\\fscx100\\fscy100}', events[1])   # braces never become tags
        self.assertIn('نرى\\\\', events[2])
        self.assertIn('0:00:00.00', events[0])

    def test_srt_for_the_owner(self):
        srt = v.srt_document(v.caption_groups(SPEECH[:2], 'en', 3))
        self.assertTrue(srt.startswith('1\n00:00:00,500 --> 00:00:01,'))


class StoreTests(unittest.TestCase):
    def test_fetch_joins_the_pieces_of_one_upload_in_order(self):
        class Store:
            NAME = 'Shorts Studio uploads'

            def json(self, method, path):
                if path.startswith('/releases?'):
                    return [{'id': 1, 'draft': True, 'name': self.NAME}, {'id': 2, 'draft': False, 'name': self.NAME}]
                return [{'id': 11, 'name': 'edit-abcd1234-001.part'}, {'id': 10, 'name': 'edit-abcd1234-000.part'},
                        {'id': 12, 'name': 'edit-ffff0000-000.part'}, {'id': 13, 'name': 'library.json'}]

            def download(self, asset):
                return {10: b'first-', 11: b'second'}[asset['id']]

        with tempfile.TemporaryDirectory() as root:
            target = Path(root) / 'source.mp4'
            v.fetch('abcd1234', 2, target, store=Store(), source={})
            self.assertEqual(target.read_bytes(), b'first-second')
            with self.assertRaises(v.EditFailure):
                v.fetch('abcd1234', 3, target, store=Store(), source={})
            with self.assertRaises(v.EditFailure):
                v.fetch('../x', 1, target, store=Store(), source={})

    class Library:
        def __init__(self, films=()):
            self.added, self.saved, self.uploaded, self.deleted = [], [], [], []
            self.films = list(films)

        def add(self, entry, folder, run):
            self.added.append((entry, run))

        def save(self, entry):
            self.saved.append(entry)
            self.films = [entry] + [f for f in self.films if f['run'] != entry['run']]

        def upload(self, name, content, content_type):
            self.uploaded.append(name)
            if name == 'library.json':
                self.films = json.loads(content)
            return len(self.uploaded)

        def assets(self):
            return [{'id': 9, 'name': 'film-55.mp4'}, {'id': 10, 'name': 'film-55.json'}, {'id': 11, 'name': 'film-56.mp4'}]

        def delete(self, asset):
            self.deleted.append(asset['id'])

        def index(self):
            return list(self.films)

    RESULT = {'state': 'ready', 'writer': 'claude', 'title': 'My take', 'description': 'd', 'language': 'ar',
              'languageName': 'Arabic', 'hashtags': ['#x'], 'youtube': {'title': 'My take'}, 'originalSeconds': 95,
              'seconds': 61.4, 'cuts': 14, 'captions': True, 'summary': 'Cut two retakes.', 'name': 'clip.mov',
              'graphics': {'title': 2, 'endcard': 1},
              'sources': [{'title': 'Photo: Sky', 'url': 'https://commons.wikimedia.org/wiki/File:Sky.jpg', 'credit': 'A (CC BY)',
                           'thumb': 'https://upload.wikimedia.org/s.jpg', 'id': 1}]}

    def folder(self, root, count):
        (Path(root) / 'edit.json').write_text(json.dumps({'state': 'ready', 'shorts': count}))
        for n in range(1, count + 1):
            short = Path(root) / str(n)
            short.mkdir()
            (short / 'edit.json').write_text(json.dumps({**self.RESULT, 'title': f'Take {n}'}))
            (short / 'video.mp4').write_bytes(b'mp4')
            (short / 'plan.json').write_text('{"version": 1}')

    def test_save_adds_an_edit_and_its_plan_and_keeps_the_upload_for_fixes(self):
        with tempfile.TemporaryDirectory() as root:
            self.folder(root, 1)
            (Path(root) / '1' / 'preview.mp4').write_bytes(b'caption-free preview')
            library = self.Library()
            result = v.save(root, 'abcd1234', run='77', library=library, source={}, parts=3)
        self.assertEqual(result, {'saved': True, 'runs': ['77']})
        entry = library.saved[0]
        self.assertEqual((entry['kind'], entry['writer'], entry['seconds'], entry['language']), ('edit', 'claude', 61, 'ar'))
        self.assertEqual(entry['editDetail'], '1:35 → 1:01 · 14 cuts · voice cleaned · captions in Arabic · 3 graphics')
        self.assertEqual((entry['upload'], entry['parts'], entry['plan']), ('abcd1234', 3, True))
        self.assertEqual(entry['sources'], [{'title': 'Photo: Sky', 'url': 'https://commons.wikimedia.org/wiki/File:Sky.jpg', 'credit': 'A (CC BY)'}])
        self.assertEqual(library.uploaded, ['film-77.json', 'film-77-preview.mp4'])
        self.assertTrue(entry['captionPreview'])
        self.assertGreater(entry['keepUntil'], time.time() + 6 * 86400)

    def test_several_shorts_get_their_own_library_entries(self):
        with tempfile.TemporaryDirectory() as root:
            self.folder(root, 3)
            library = self.Library()
            result = v.save(root, 'abcd1234', run='77', library=library, source={}, parts=1)
        self.assertEqual(result['runs'], ['7701', '7702', '7703'])
        self.assertEqual([e['editDetail'].split(' · ')[0] for e in library.saved], ['Short 1 of 3', 'Short 2 of 3', 'Short 3 of 3'])
        self.assertEqual([e['title'] for e in library.saved], ['Take 1', 'Take 2', 'Take 3'])

    def test_a_fixed_short_replaces_the_old_version(self):
        with tempfile.TemporaryDirectory() as root:
            self.folder(root, 1)
            library = self.Library([{'run': '55', 'title': 'Old'}, {'run': '56', 'title': 'Other'}])
            v.save(root, 'abcd1234', run='88', library=library, source={}, parts=1, replaces='55')
        self.assertEqual(sorted(library.deleted), [9, 10])                   # film-55 files, not film-56
        self.assertEqual([f['run'] for f in library.films], ['88', '56'])
        self.assertTrue(library.saved[0]['editDetail'].endswith('fixed by you'))

    def test_a_failed_edit_is_not_saved(self):
        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'edit.json').write_text(json.dumps({'state': 'failed', 'message': 'Too long'}))
            self.assertEqual(v.save(root, 'abcd1234', run='1', library=object(), source={}), {'saved': False})


class ClaudeTests(unittest.TestCase):
    def fake_claude(self, root, reply, code=0):
        binary = Path(root) / 'claude'
        binary.write_text('#!/usr/bin/env python3\nimport json, os, sys\nprompt = sys.stdin.read()\n'
                          'json.dump({"argv": sys.argv[1:], "env": sorted(os.environ), "prompt": prompt[:80]}, open(%r, "w"))\n'
                          'print(json.dumps({"type": "result", "is_error": False, "result": %r}))\nsys.exit(%d)\n'
                          % (str(Path(root) / 'seen.json'), reply, code))
        binary.chmod(0o755)
        return str(binary)

    def test_claude_edits_with_no_tools_and_no_other_secrets(self):
        source = {'PATH': '/usr/bin:/bin', 'CLAUDE_CODE_OAUTH_TOKEN': 'sk-ant-oat01-' + 'x' * 40,
                  'GITHUB_TOKEN': 'ghs_secret', 'STUDIO_NOTIFY_KEY': 'k'}
        with tempfile.TemporaryDirectory() as root, mock.patch('claude_usage.require_usage', return_value={}):
            ask, issue = v.claude_editor(source, self.fake_claude(root, '{"cut": []}'))
            self.assertEqual(issue, '')
            self.assertEqual(ask(v.editor_prompt(SPEECH, 'en', 9)), '{"cut": []}')
            seen = json.loads((Path(root) / 'seen.json').read_text())
        self.assertNotIn('GITHUB_TOKEN', seen['env'])
        self.assertNotIn('STUDIO_NOTIFY_KEY', seen['env'])
        self.assertEqual(seen['argv'][seen['argv'].index('--tools') + 1], '')
        self.assertIn('senior short-form video editor', seen['prompt'])

    def test_pictures_reach_claude_as_images(self):
        source = {'PATH': '/usr/bin:/bin', 'CLAUDE_CODE_OAUTH_TOKEN': 'sk-ant-oat01-' + 'x' * 40}
        with tempfile.TemporaryDirectory() as root, mock.patch('claude_usage.require_usage', return_value={}):
            ask, _ = v.claude_editor(source, self.fake_claude(root, '{"1": 2}'))
            self.assertEqual(ask('Pick photos', [b'\x89PNG-one', b'\x89PNG-two']), '{"1": 2}')
            seen = json.loads((Path(root) / 'seen.json').read_text())
        self.assertEqual(seen['argv'][seen['argv'].index('--input-format') + 1], 'stream-json')
        self.assertTrue(seen['prompt'].startswith('{"type": "user"'))

    def test_without_claude_the_reason_is_kept(self):
        from claude_usage import UsageBlocked
        source = {'PATH': '/usr/bin', 'CLAUDE_CODE_OAUTH_TOKEN': 'sk-ant-oat01-' + 'x' * 40}
        with tempfile.TemporaryDirectory() as root:
            binary = self.fake_claude(root, '')
            self.assertEqual(v.claude_editor({'PATH': '/usr/bin'}, binary)[1], 'Claude is not signed in. Sign in from Account in Shorts Studio.')
            blocked = UsageBlocked('Video creation paused: Claude session usage is 91%.')
            with mock.patch('claude_usage.require_usage', side_effect=blocked):
                ask, issue = v.claude_editor(source, binary)
        self.assertIsNone(ask)
        self.assertEqual(issue, 'Claude was skipped to stay within your usage limits: Claude session usage is 91%.')


class AccuracyTests(unittest.TestCase):
    def test_claude_proofreads_captions_word_for_word_or_by_length(self):
        heard = words((' الدعا', 0.0, 0.4), (' انفذ', 0.45, 0.8), (' من', 0.85, 0.95), (' السنان', 1.0, 1.5),
                      (' اشتركوا', 3.0, 3.4), (' في', 3.45, 3.5), (' القناة', 3.55, 4.0), (' قال', 5.0, 5.3), (' تعالى', 5.35, 5.8))
        answer = json.dumps({'cut': [], 'captions': [
            {'from': 0, 'to': 1, 'text': 'الدعاءُ أنفذُ'}, {'from': 2, 'to': 3, 'text': 'من السنان.'},
            {'from': 4, 'to': 6, 'text': ''},                                   # a hallucination in silence
            {'from': 7, 'to': 8, 'text': 'قال الله تعالى:'},                     # three words for two heard
            {'from': 1, 'to': 2, 'text': 'overlaps'}]})                        # out of order: ignored
        plan = v.parse_plan(answer, heard)
        self.assertEqual(len(plan['captions']), 4)
        tokens = v.caption_tokens(heard, set(), plan['captions'], 'ar')
        self.assertEqual([t['w'] for t in tokens], ['الدعاءُ', ' أنفذُ', 'من', ' السنان.', 'قال', ' الله', ' تعالى:'])
        self.assertEqual((tokens[0]['s'], tokens[1]['e']), (0.0, 0.8))          # same count: the heard timings
        self.assertEqual((tokens[4]['s'], round(tokens[-1]['e'], 3)), (5.0, 5.8))   # spread over the line
        self.assertLess(tokens[4]['e'], tokens[5]['s'] + 1e-9)
        groups = v.caption_groups(tokens, 'ar', 7)
        self.assertEqual([''.join(w['w'] for w in g['words']).strip() for g in groups],
                         ['الدعاءُ أنفذُ', 'من السنان.', 'قال الله تعالى:'])

    def test_words_no_line_covers_are_captioned_as_heard_and_cut_words_never(self):
        plan = v.parse_plan(json.dumps({'cut': [{'from': 1, 'to': 1}], 'captions': [{'from': 2, 'to': 3, 'text': 'This is'}]}), SPEECH)
        tokens = v.caption_tokens(SPEECH, plan['removed'], plan['captions'], 'en')
        texts = [t['w'].strip() for t in tokens]
        self.assertEqual(texts[:4], ['Hello', 'This', 'is', 'a'])
        self.assertNotIn('um', texts)


class VisualTests(unittest.TestCase):
    PLAN = {'cut': [{'from': 1, 'to': 1}], 'hook': 'The real take', 'theme': 'teal', 'visuals': [
        {'type': 'title', 'at': 6, 'text': 'The real take'}, {'type': 'emoji', 'at': 9, 'emoji': '🎬'},
        {'type': 'point', 'at': 8, 'number': 1, 'text': 'Works great'}, {'type': 'emoji', 'at': 10, 'emoji': 'ok'},
        {'type': 'quote', 'from': 11, 'to': 14, 'text': 'It works great', 'source': 'Me', 'kind': 'nonsense'},
        {'type': 'picture', 'from': 2, 'to': 4, 'query': 'film camera!', 'about': 'a camera'}, {'type': 'video'},
        {'type': 'title', 'at': 99, 'text': 'out of range'}, {'type': 'title', 'at': 3, 'text': ''}]}

    def test_visuals_are_checked(self):
        plan = v.parse_plan(json.dumps(self.PLAN), SPEECH)
        kinds = [x['type'] for x in plan['visuals']]
        self.assertEqual(kinds, ['title', 'emoji', 'point', 'quote', 'picture'])
        self.assertEqual(plan['visuals'][3]['kind'], 'quote')
        self.assertEqual(plan['visuals'][4]['query'], 'film camera')
        self.assertEqual((plan['hook'], plan['theme']), ('The real take', 'teal'))
        self.assertEqual(v.parse_plan(json.dumps({'theme': 'neon'}), SPEECH)['theme'], 'gold')

    def test_top_graphics_never_overlap_and_titles_make_room(self):
        times = {i: (w['s'], w['e']) for i, w in enumerate(SPEECH)}
        visuals = [{'type': 'title', 'from': 6, 'to': 6, 'text': 'A'}, {'type': 'point', 'from': 9, 'to': 9, 'number': 1, 'text': 'B'},
                   {'type': 'title', 'from': 10, 'to': 10, 'text': 'C'}, {'type': 'emoji', 'from': 12, 'to': 12, 'emoji': '🎬'},
                   {'type': 'quote', 'from': 12, 'to': 14, 'text': 'Q', 'source': '', 'kind': 'quote'}]
        out = v.schedule_visuals(visuals, 'Hook', times, 9.0)
        # The point collides with the quote, which matters more, so it goes; the title is shortened.
        self.assertEqual([x['type'] for x in out], ['hook', 'title', 'quote'])
        top = [x for x in out if x['type'] in v.TOP]
        for a, b in zip(top, top[1:]):
            self.assertLessEqual(a['end'], b['start'])
        self.assertAlmostEqual(out[1]['end'], out[2]['start'] - 0.15, places=3)   # the title was shortened
        self.assertEqual(out[0]['start'], 0.12)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'needs FFmpeg')
    def test_graphics_render_as_transparent_clips_in_arabic_and_english(self):
        import edit_graphics as g
        from PIL import Image
        with tempfile.TemporaryDirectory() as root:
            for language, text in (('ar', 'الدعاء أنفذ من السنان'), ('en', 'Stop wasting mornings')):
                visuals = [{'type': 'hook', 'text': text, 'start': 0.1, 'end': 1.0},
                           {'type': 'quote', 'text': text, 'source': 'Source', 'kind': 'verse', 'start': 1.2, 'end': 2.2},
                           {'type': 'point', 'number': 2, 'text': text, 'start': 2.4, 'end': 3.2},
                           {'type': 'picture', 'id': 1, 'start': 3.4, 'end': 4.2}]
                clips = g.render_clips(visuals, (540, 960), 30, Path(root) / language, language, g.theme('gold'),
                                       {1: Image.new('RGB', (800, 600), (40, 120, 200))})
                self.assertEqual([c['type'] for c in clips], ['picture', 'hook', 'quote', 'point'])
                for clip in clips:
                    info = json.loads(subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v', '-show_entries',
                                                      'stream=codec_name,pix_fmt,width,height', '-of', 'json', clip['path']],
                                                     capture_output=True, text=True).stdout)['streams'][0]
                    self.assertEqual((info['codec_name'], info['pix_fmt']), ('qtrle', 'argb'))   # keeps transparency
                    self.assertLessEqual(clip['x'] + info['width'], 540)
                    self.assertLessEqual(clip['y'] + info['height'], 960)
                self.assertEqual((clips[0]['x'], clips[0]['y']), (0, 0))           # pictures fill the screen

    def test_claude_chooses_pictures_after_seeing_them(self):
        import io
        import edit_graphics as g
        from PIL import Image
        photo = io.BytesIO()
        Image.new('RGB', (1280, 853), (200, 120, 40)).save(photo, 'JPEG')
        search = {'query': {'pages': {
            '1': {'index': 1, 'imageinfo': [{'mime': 'image/jpeg', 'width': 4000, 'height': 2667,
                  'thumburl': 'https://upload.wikimedia.org/a.jpg', 'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Old_spear.jpg',
                  'extmetadata': {'Artist': {'value': '<a href="x">Jane Doe</a>'}, 'LicenseShortName': {'value': 'CC BY-SA 4.0'}}}]},
            '2': {'index': 2, 'imageinfo': [{'mime': 'image/svg+xml', 'width': 4000, 'height': 2667, 'thumburl': 'https://upload.wikimedia.org/b.png'}]},
            '3': {'index': 3, 'imageinfo': [{'mime': 'image/jpeg', 'width': 300, 'height': 200, 'thumburl': 'https://upload.wikimedia.org/c.jpg'}]},
            '4': {'index': 4, 'imageinfo': [{'mime': 'image/jpeg', 'width': 3000, 'height': 2000, 'thumburl': 'https://evil.example/d.jpg'}]},
            '5': {'index': 5, 'imageinfo': [{'mime': 'image/jpeg', 'width': 4000, 'height': 2667,
                  'thumburl': 'https://upload.wikimedia.org/unlicensed.jpg', 'descriptionurl': 'https://commons.wikimedia.org/wiki/File:Unknown.jpg'}]}}}}
        asked = []

        class Response(io.BytesIO):
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        def opener(request, timeout=None):
            asked.append(request.full_url)
            self.assertEqual(request.get_header('User-agent'), g.USER_AGENT)
            return Response(json.dumps(search).encode() if 'api.php' in request.full_url else photo.getvalue())

        def claude(prompt, images):
            self.assertIn('holy figure', prompt)
            self.assertEqual(len(images), 2)
            self.assertTrue(images[0].startswith(b'\x89PNG'))
            return '{"1": 1, "2": 0}'

        chosen, credits = g.choose_pictures([{'id': 1, 'query': 'spear', 'about': 'an old spear'}, {'id': 2, 'query': 'night sky'}],
                                            claude, opener=opener)
        self.assertEqual(list(chosen), [1])
        self.assertEqual(credits, [{'title': 'Photo: Old spear', 'url': 'https://commons.wikimedia.org/wiki/File:Old_spear.jpg',
                                    'credit': 'Jane Doe · CC BY-SA 4.0 · Cropped and animated',
                                    'thumb': 'https://upload.wikimedia.org/a.jpg', 'id': 1}])
        self.assertFalse(any('evil.example' in url for url in asked))        # only Wikimedia's own image host
        self.assertFalse(any('unlicensed.jpg' in url for url in asked))


@unittest.skipUnless(shutil.which('ffmpeg'), 'needs FFmpeg')
class TranscribeTests(unittest.TestCase):
    def test_whisper_gets_samples_not_a_file(self):
        """faster-whisper decodes files with PyAV, and PyAV 15+ rejects its options: hand it samples."""
        import sys
        import types
        seen = {}

        class Word:
            def __init__(self, word, start, end):
                self.word, self.start, self.end, self.probability = word, start, end, 0.9

        class Model:
            def __init__(self, name, **options):
                seen['model'] = name

            def transcribe(self, audio, **options):
                seen['audio'], seen['options'] = audio, options
                segment = types.SimpleNamespace(words=[Word(' مرحبا', 0.1, 0.5), Word(' بكم', 0.6, 0.9)])
                return iter([segment]), types.SimpleNamespace(language='ar', language_probability=0.97)

        with tempfile.TemporaryDirectory() as root, mock.patch.dict(sys.modules, {'faster_whisper': types.SimpleNamespace(WhisperModel=Model)}):
            wav = Path(root) / 'speech.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=f=300:d=1.5', '-ac', '1', '-ar', '16000',
                            '-c:a', 'pcm_s16le', str(wav)], check=True)
            heard = v.transcribe(wav, 1.5)
        audio = seen['audio']
        self.assertNotIsInstance(audio, (str, Path))
        self.assertEqual((str(audio.dtype), audio.shape), ('float32', (24000,)))
        self.assertLessEqual(float(abs(audio).max()), 1.0)
        self.assertEqual(seen['model'], 'large-v3')                 # the most accurate model for normal lengths
        self.assertEqual(seen['options']['hallucination_silence_threshold'], 2.0)
        # The language found first is then fixed, with a punctuated opening in that language.
        self.assertEqual(seen['options']['language'], 'ar')
        self.assertEqual(seen['options']['initial_prompt'], v.OPENINGS['ar'])
        self.assertEqual((heard['language'], [w['w'] for w in heard['words']]), ('ar', [' مرحبا', ' بكم']))

    def test_the_speech_track_must_be_what_ffmpeg_made(self):
        with tempfile.TemporaryDirectory() as root:
            wav = Path(root) / 'stereo.wav'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'sine=d=0.2', '-ac', '2', '-ar', '44100', str(wav)], check=True)
            with self.assertRaises(RuntimeError):
                v.load_speech(wav)


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs FFmpeg')
class RenderTests(unittest.TestCase):
    def test_a_phone_video_is_cut_cleaned_and_captioned_in_sync(self):
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'raw.mp4'
            # A rotated phone clip: a white flash and a beep together every 1.7 s, with noise between.
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=black:size=480x270:rate=30000/1001',
                            '-f', 'lavfi', '-i', 'sine=f=600:r=48000', '-f', 'lavfi', '-i', 'anoisesrc=a=0.01:r=48000',
                            '-filter_complex', "[0:v]drawbox=c=white:t=fill:enable='lt(mod(t,1.7),0.4)'[v];"
                            "[1:a]volume='if(lt(mod(t,1.7),0.4),0.6,0)':eval=frame[b];[b][2:a]amix=inputs=2:normalize=0[a]",
                            '-map', '[v]', '-map', '[a]', '-t', '12', '-c:v', 'libx264', '-c:a', 'aac', str(root / 'flat.mp4')], check=True)
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-display_rotation:v', '90', '-i', str(root / 'flat.mp4'),
                            '-c', 'copy', str(source)], check=True)
            beeps = [{'w': ' beep', 's': k * 1.7 + 0.02, 'e': k * 1.7 + 0.38, 'p': 1} for k in range(7)]

            def editor(prompt):
                return json.dumps({'cut': [{'from': 2, 'to': 2, 'why': 'retake'}], 'emphasis': [], 'title': 'Beeps',
                                   'description': 'Beeps?', 'hashtags': ['#beep'], 'category': 'education', 'summary': 'Cut one.'})

            result = v.edit(source, root / 'out', name='raw.mp4', editor=editor, options={'ending': False, 'teaser': False, 'fix': False},
                            transcriber=lambda wav, d: {'language': 'en', 'words': v.clean_words(beeps, d)})['results'][0]
            out = root / 'out' / '1'
            info = v.probe(out / 'video.mp4')
            self.assertEqual((info['width'], info['height']), (270, 480))      # upright, as filmed
            self.assertEqual((result['writer'], result['wordsCut'], result['cuts']), ('claude', 1, 5))
            self.assertLess(result['seconds'], 9)
            self.assertAlmostEqual(info['duration'], result['seconds'], delta=0.05)
            for name in ('video.png', 'captions.srt', 'edit.json', 'plan.json'):
                self.assertTrue((out / name).is_file(), name)
            log = lambda args: subprocess.run(['ffmpeg', '-i', str(out / 'video.mp4'), *args, '-f', 'null', '-'],
                                              capture_output=True, text=True).stderr
            sound = [float(x) for x in re.findall(r'silence_end: ([\d.]+)', log(['-af', 'silencedetect=n=-24dB:d=0.1']))]
            picture = [float(x) for x in re.findall(r'black_end:([\d.]+)', log(['-vf', 'blackdetect=d=0.05:pix_th=0.3', '-an']))]
            self.assertGreaterEqual(len(picture), 5)
            for flash, beep in list(zip(picture, sound))[:5]:   # the last pair marks the end of the file
                self.assertLess(abs(flash - beep), 0.04)                       # lips stay in sync after every cut
            loud = log(['-af', 'ebur128', '-vn'])
            integrated = float(re.findall(r'I:\s+(-?[\d.]+) LUFS', loud)[-1])
            self.assertAlmostEqual(integrated, -14, delta=1.5)


if __name__ == '__main__':
    unittest.main()
