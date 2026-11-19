import json
import re
import shutil
import subprocess
import tempfile
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
        self.assertIn('Noto Sans Arabic', document)
        self.assertRegex(document, r'Style: Caption,.*,-1\n')                # automatic base direction
        events = re.findall(r'^Dialogue: .*$', document, re.M)
        self.assertEqual(len(events), 3)
        self.assertIn('{\\c&H0000D7FF&} (سوف){\\c&H00FFFFFF&}', events[1])   # braces never become tags
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

    def test_save_adds_an_edit_to_the_library_and_removes_the_upload(self):
        class Library:
            added, saved = [], []

            def add(self, entry, folder, run):
                self.added.append((entry, run))

            def save(self, entry):
                self.saved.append(entry)

        class Store:
            NAME, deleted = 'Shorts Studio uploads', []

            def json(self, method, path):
                return [{'id': 1, 'draft': True, 'name': self.NAME}] if '?' in path and 'releases?' in path else \
                    [{'id': 5, 'name': 'edit-abcd1234-000.part'}]

            def delete(self, asset):
                self.deleted.append(asset['id'])

        with tempfile.TemporaryDirectory() as root:
            (Path(root) / 'edit.json').write_text(json.dumps({
                'state': 'ready', 'writer': 'claude', 'title': 'My take', 'description': 'd', 'language': 'ar',
                'languageName': 'Arabic', 'hashtags': ['#x'], 'youtube': {'title': 'My take'}, 'originalSeconds': 95,
                'seconds': 61.4, 'cuts': 14, 'captions': True, 'summary': 'Cut two retakes.', 'name': 'clip.mov'}))
            library, store = Library(), Store()
            result = v.save(root, 'abcd1234', run='77', library=library, store=store, source={})
        self.assertEqual(result, {'saved': True, 'run': '77'})
        entry = library.saved[0]
        self.assertEqual((entry['kind'], entry['writer'], entry['seconds'], entry['language']), ('edit', 'claude', 61, 'ar'))
        self.assertEqual(entry['editDetail'], '1:35 → 1:01 · 14 cuts · voice cleaned · captions in Arabic')
        self.assertEqual(entry['youtubePack'], {'title': 'My take'})
        self.assertEqual(store.deleted, [5])

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

            result = v.edit(source, root / 'out', name='raw.mp4', editor=editor,
                            transcriber=lambda wav, d: {'language': 'en', 'words': v.clean_words(beeps, d)})
            info = v.probe(root / 'out' / 'video.mp4')
            self.assertEqual((info['width'], info['height']), (270, 480))      # upright, as filmed
            self.assertEqual((result['writer'], result['wordsCut'], result['cuts']), ('claude', 1, 5))
            self.assertLess(result['seconds'], 9)
            self.assertAlmostEqual(info['duration'], result['seconds'], delta=0.05)
            for name in ('video.png', 'captions.srt', 'edit.json'):
                self.assertTrue((root / 'out' / name).is_file(), name)
            log = lambda args: subprocess.run(['ffmpeg', '-i', str(root / 'out' / 'video.mp4'), *args, '-f', 'null', '-'],
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
