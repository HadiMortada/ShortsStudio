"""Best moments, teaser, ending, caption styles, translation, speakers, framing, picture fix,
sound effects and fixing a Short from its saved plan."""
import json
import math
import re
import shutil
import subprocess
import tempfile
import unittest
import wave
from pathlib import Path
from unittest.mock import patch

import edit_frame as frame
import edit_sound as sound
import video_edit as v


def timed(text, start=0.0, step=0.4, pause_every=0, pause=0.9):
    out, t = [], start
    for n, word in enumerate(text.split()):
        out.append({'w': ' ' + word, 's': round(t, 3), 'e': round(t + step * 0.8, 3)})
        t += step + (pause if pause_every and n % pause_every == pause_every - 1 else 0)
    return out


class OptionTests(unittest.TestCase):
    def test_options_are_checked(self):
        self.assertEqual(v.edit_options(None), v.OPTION_DEFAULTS)
        self.assertEqual(v.edit_options('{"mode": "moments", "translate": "en", "sfx": true, "captions": "neon", "fix": "yes"}'),
                         {**v.OPTION_DEFAULTS, 'mode': 'moments', 'translate': 'en', 'sfx': True})
        self.assertEqual(v.edit_options('not json'), v.OPTION_DEFAULTS)

    def test_vertical_shorts_and_upright_sources(self):
        self.assertEqual(v.target_size({'width': 1920, 'height': 1080}, 'vertical'), (1080, 1920))
        self.assertEqual(v.target_size({'width': 720, 'height': 1280}, 'vertical'), (720, 1280))
        self.assertEqual(v.target_size({'width': 1920, 'height': 1080}, 'original'), (1920, 1080))


class MomentTests(unittest.TestCase):
    WORDS = timed(' '.join(f'w{n}.' if n % 10 == 9 else f'w{n}' for n in range(300)), pause_every=10)

    def test_moments_are_checked(self):
        answer = json.dumps({'moments': [{'from': 0, 'to': 59, 'title': 'First'}, {'from': 30, 'to': 80},   # overlaps: dropped
                                         {'from': 100, 'to': 105}, {'from': 120, 'to': 189, 'title': 'Second'},   # too short: dropped
                                         {'from': 290, 'to': 400}]})                                              # out of range
        self.assertEqual(v.parse_moments(answer, self.WORDS), [(0, 59, 'First'), (120, 189, 'Second')])
        with self.assertRaises(ValueError):
            v.parse_moments('{"moments": []}', self.WORDS)

    def test_without_claude_moments_end_at_the_longest_pauses(self):
        moments = v.auto_moments(self.WORDS)
        self.assertEqual(len(moments), 3)
        for a, b, _ in moments:
            self.assertTrue(self.WORDS[b]['w'].endswith('.'))            # cut where the speaker paused
            self.assertGreaterEqual(self.WORDS[b]['e'] - self.WORDS[a]['s'], 12)
        self.assertEqual(moments[1][0], moments[0][1] + 1)

    def test_the_prompt_marks_speakers(self):
        prompt = v.moments_prompt(self.WORDS[:4], 'ar', 600, speakers=[0, 0, 1, 1])
        self.assertIn('2\t0.80\t[S2] w2', prompt)
        self.assertIn('Choose 3 to 5 moments', prompt)


class TeaserTests(unittest.TestCase):
    WORDS = timed('one two three four five six seven eight nine ten eleven twelve thirteen fourteen fifteen sixteen')

    def test_a_teaser_is_a_short_complete_line_from_later(self):
        self.assertEqual(v.parse_teaser({'from': 10, 'to': 14}, self.WORDS, set()), (10, 14))
        self.assertIsNone(v.parse_teaser({'from': 1, 'to': 5}, self.WORDS, set()))          # from the opening
        self.assertIsNone(v.parse_teaser({'from': 10, 'to': 11}, self.WORDS, set()))        # too short
        self.assertIsNone(v.parse_teaser({'from': 10, 'to': 14}, self.WORDS, {12}))         # crosses a cut
        self.assertIsNone(v.parse_teaser(None, self.WORDS, set()))

    def test_the_teaser_plays_first_and_its_words_appear_twice(self):
        pieces = v.keep_ranges(self.WORDS, set(), 7.0, 30, teaser=(10, 13))
        self.assertTrue(pieces[0]['teaser'] and pieces[1].get('flash_in'))
        self.assertAlmostEqual(pieces[0]['s'], math.floor((4.0 - 0.06) * 30) / 30, places=5)
        placed, total = v.place(self.WORDS, pieces)
        texts = [w['w'].strip() for w in placed]
        self.assertEqual(texts[:4], ['eleven', 'twelve', 'thirteen', 'fourteen'])
        self.assertEqual(texts.count('twelve'), 2)
        times = v.word_times(self.WORDS, set(), pieces)
        self.assertGreater(times[11][0], pieces[0]['e'] - pieces[0]['s'])  # graphics follow the main telling


class StyleTests(unittest.TestCase):
    def groups(self):
        tokens = [{**w, 'spk': n // 3} for n, w in enumerate(timed('مرحبا بكم جميعا أهلا وسهلا بكم'))]
        placed, total = v.place(tokens, [{'s': 0.0, 'e': 3.0}])
        return v.caption_groups(placed, 'ar', total)

    def test_speakers_get_their_own_lines_and_colours(self):
        groups = self.groups()
        self.assertEqual([g['spk'] for g in groups], [0, 1])
        document = v.ass_document(groups, 1080, 1920, 'ar')
        self.assertIn('{\\c&H00FFE0A8&}', document)                     # the second speaker's colour

    def test_styles_and_a_translated_line(self):
        groups = self.groups()
        translations = {groups[0]['key']: 'Welcome {all}'}
        boxed = v.ass_document(groups, 1080, 1920, 'ar', style='boxed', translations=translations, second_language='en')
        self.assertRegex(boxed, r'Style: Caption,[^\n]*,3,\d+,0,2,')        # an opaque box
        self.assertIn('\\N{\\fnNoto Sans\\fs', boxed)
        self.assertIn('Welcome (all)', boxed)
        clean = v.ass_document(groups, 1080, 1920, 'ar', style='clean')
        self.assertNotIn('\\t(', clean)                                     # no pop animation
        self.assertNotIn('fscx110', clean)

    def test_the_owners_caption_fixes(self):
        groups = self.groups()
        fixed = v.apply_overrides(groups, {groups[0]['key']: 'أهلًا بالجميع', groups[1]['key']: ''}, 'ar')
        self.assertEqual([v.group_text(g) for g in fixed], ['أهلًا بالجميع'])
        self.assertEqual((fixed[0]['words'][0]['s'], fixed[0]['words'][-1]['e']), (groups[0]['s'], groups[0]['e']))


class ChangeTests(unittest.TestCase):
    SAVED = {'plan': {'visuals': [{'type': 'title', 'from': 1, 'to': 1, 'text': 'A'}, {'type': 'emoji', 'from': 2, 'to': 2, 'emoji': '🎬'},
                                  {'type': 'quote', 'from': 3, 'to': 5, 'text': 'Q', 'source': 'S', 'kind': 'quote'}],
                      'hook': 'Hook', 'cuts': [{'from': 1, 'to': 2, 'why': 'retake'}, {'from': 7, 'to': 7, 'why': 'filler'}],
                      'removed': [1, 2, 7], 'captions': [[0, 2, 'x']], 'teaser': [4, 6]},
             'overrides': {'1.00': 'old fix'}, 'translations': {'1.00': 'a', '2.00': 'b'}}

    def test_changes_apply_to_a_saved_plan(self):
        changed = v.apply_changes(self.SAVED, json.dumps({'visuals': {'0': {'remove': True}, '2': {'text': 'New quote'}, '1': {'text': 'ignored'}},
                                                          'hook': '', 'restore': [0], 'captions': {'2.00': 'Fixed'}}))
        plan = changed['plan']
        self.assertEqual([x['type'] for x in plan['visuals']], ['emoji', 'quote'])
        self.assertEqual(plan['visuals'][1]['text'], 'New quote')
        self.assertEqual(plan['hook'], '')
        self.assertEqual(plan['removed'], [7])
        self.assertEqual(plan['teaser'], (4, 6))
        self.assertEqual(changed['overrides'], {'1.00': 'old fix', '2.00': 'Fixed'})
        self.assertEqual(changed['translations'], {'1.00': 'a'})              # a re-worded line is translated again


class FrameTests(unittest.TestCase):
    INFO = {'width': 1920, 'height': 1080}

    def test_the_crop_follows_the_face_and_long_takes_are_reframed(self):
        track = [(t / 2, (0.2 if t < 16 else 0.7, 0.4, 0.3)) for t in range(40)]
        self.assertEqual(frame.layout_for(self.INFO, (1080, 1920), track), 'crop')
        pieces = frame.frame_pieces([{'s': 0.0, 'e': 20.0, 'zoom': 1.0}, {'s': 20.0, 'e': 22.0, 'zoom': 1.16}],
                                    track, self.INFO, (1080, 1920), 30, 'crop')
        self.assertEqual(len(pieces), 3)                                     # reframed when the speaker moved
        first, second, punched = pieces
        self.assertEqual((first['crop'][2], first['crop'][3]), (606, 1080))
        self.assertLess(first['crop'][0], second['crop'][0])
        self.assertAlmostEqual(second['s'], 9.0, places=3)
        self.assertLess(punched['crop'][2], first['crop'][2])                # the punch-in centres on the face
        centre = punched['crop'][0] + punched['crop'][2] / 2
        self.assertAlmostEqual(centre / 1920, 0.7, delta=0.02)

    def test_no_face_means_the_whole_picture_over_a_blur(self):
        self.assertEqual(frame.layout_for(self.INFO, (1080, 1920), [(0, None)] * 10), 'blur')
        self.assertEqual(frame.layout_for(self.INFO, (1920, 1080), []), 'crop')        # same shape: nothing to follow
        box = frame.crop_box(self.INFO, (1080, 1920), 1.0, None, 'blur')
        self.assertEqual(box, (0, 0, 1920, 1080))

    def test_shake_is_measured_from_vid_stab_motion(self):
        def trf(jitter):
            lines = []
            for n in range(60):
                dx = round(jitter * math.sin(n * 2.1))
                lines.append(f'Frame {n} (List 3 [(LM {dx} 0 10 10 20 0.5 0.9),(LM {dx} 1 30 30 20 0.5 0.9),(LM {dx} -1 50 50 20 0.5 0.9)])')
            return '\n'.join(lines)
        self.assertLess(frame.shake(trf(0)), 0.5)
        self.assertGreater(frame.shake(trf(4)), 2)

    def test_a_dark_picture_is_brightened(self):
        import numpy as np
        dark = [(n / 2, np.full((90, 160, 3), 45, np.uint8)) for n in range(12)]
        bright = [(n / 2, np.full((90, 160, 3), 120, np.uint8)) for n in range(12)]
        fix = frame.picture_fix(dark)
        gamma = float(re.search(r'gamma=([\d.]+)', fix).group(1))
        self.assertGreater(gamma, 1.2)                                       # eq brightens above 1
        self.assertNotIn('gamma', frame.picture_fix(bright))


class SoundTests(unittest.TestCase):
    def test_speakers_are_numbered_by_how_much_they_speak(self):
        words = timed(' '.join(['a'] * 30))
        turns = [(0, 4.0, 5), (4.0, 9.0, 2), (9.0, 9.3, 7), (9.3, 12.0, 5)]
        labels = sound.assign_speakers(words, turns)
        self.assertEqual(labels[0], 0)
        self.assertEqual(labels[12], 1)
        self.assertEqual(len(set(labels)), 2)                                # the 0.3 s blip is folded in
        self.assertEqual(sound.assign_speakers(words, [(0, 12, 1)]), [])     # one speaker: no colours

    def test_effects_track_is_quiet_and_the_right_length(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as root:
            path = sound.sfx_track([(0.5, 'title'), (1.0, 'emoji'), (2.0, 'endcard'), (2.5, 'unknown')], 3.0, Path(root) / 'fx.wav')
            with wave.open(str(path)) as track:
                self.assertEqual((track.getnchannels(), track.getframerate(), track.getnframes()), (2, 48000, 144000))
                data = np.frombuffer(track.readframes(track.getnframes()), '<i2')
        self.assertTrue(0 < np.abs(data).max() < 32767 * 0.5)

    def test_restrained_effects_and_meaningful_silence(self):
        import numpy as np
        with tempfile.TemporaryDirectory() as root:
            cues = [(0.5, 'picture'), (1.5, 'picture'), (2.5, 'picture'), (5.2, 'transition'), (7, 'picture')]
            track_path = sound.sfx_track(cues, 8, Path(root) / 'sfx.wav')
            with wave.open(str(track_path), 'rb') as stream:
                data = np.frombuffer(stream.readframes(stream.getnframes()), '<i2').reshape(-1, 2)
        self.assertTrue(data[int(.55 * sound.RATE):int(.6 * sound.RATE)].any())
        self.assertFalse(data[int(1.5 * sound.RATE):int(4.9 * sound.RATE)].any())
        self.assertTrue(data[int(5.2 * sound.RATE):int(5.3 * sound.RATE)].any())
        self.assertFalse(data[int(7 * sound.RATE):int(7.2 * sound.RATE)].any())

    def test_accepts_only_the_pinned_free_effects_and_spaces_out_cues(self):
        import hashlib
        import io
        import numpy as np
        raw = io.BytesIO()
        with wave.open(raw, 'wb') as sample:
            sample.setparams((1, 2, 24000, 0, 'NONE', 'not compressed'))
            sample.writeframes(np.full(2400, 5000, dtype='<i2').tobytes())
        data = raw.getvalue()
        expected = hashlib.sha256(data).hexdigest()
        files = {'transition': ('good.wav', expected), 'accent': ('bad.wav', 'wrong')}

        def opener(request, timeout):
            self.assertTrue(request.full_url.startswith(sound.EFFECT_MIRROR))
            return io.BytesIO(data)

        with patch.object(sound, 'EFFECT_FILES', files):
            samples = sound.effect_samples(opener)
        self.assertEqual(set(samples), {'transition'})
        self.assertEqual(len(samples['transition']), 4800)
        with tempfile.TemporaryDirectory() as root:
            path = sound.sfx_track([(0.2, 'picture'), (0.3, 'emoji'), (0.6, 'quote'), (1.5, 'endcard')],
                                   2, Path(root) / 'fx.wav', samples)
            with wave.open(str(path), 'rb') as track:
                audio = np.frombuffer(track.readframes(track.getnframes()), '<i2').reshape(-1, 2)
        self.assertTrue(audio[12000:14000].any())
        self.assertFalse(audio[int(.45 * sound.RATE):int(1.4 * sound.RATE)].any())
        self.assertTrue(audio[73000:].any())


@unittest.skipUnless(shutil.which('ffmpeg') and shutil.which('ffprobe'), 'needs FFmpeg')
class ShortsTests(unittest.TestCase):
    def test_best_moments_become_shorts_that_can_be_fixed(self):
        words = timed(' '.join(f'word{n}' for n in range(70)), start=0.3, step=0.42, pause_every=7)
        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            source = root / 'talk.mp4'
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=320x180:rate=30', '-f', 'lavfi',
                            '-i', 'sine=f=220:r=48000', '-t', '35', '-c:v', 'libx264', '-c:a', 'aac', str(source)], check=True)
            calls = []

            def editor(prompt, images=()):
                calls.append('images' if images else prompt.split('\n')[0][:40])
                if 'cut into separate' in prompt:
                    return json.dumps({'moments': [{'from': 0, 'to': 27, 'title': 'One'}, {'from': 35, 'to': 62, 'title': 'Two'}]})
                if images:
                    return '{"drop": ["G1"], "cover": 2}'
                if 'Translate' in prompt:
                    count = int(re.search(r'exactly (\d+) entries', prompt).group(1))
                    return json.dumps({'lines': [f'line {n}' for n in range(count)]})
                first_index = 35 if 'word35' in prompt and 'word0' not in prompt else 0
                return json.dumps({'cut': [{'from': 2, 'to': 2, 'why': 'filler'}], 'hook': f'word{first_index + 20}', 'theme': 'blue',
                                   'captions': [{'from': 0, 'to': 1, 'text': 'Word Zero, word one.'}],
                                   'visuals': [{'type': 'title', 'at': 8, 'text': f'word{first_index + 8}'}, {'type': 'emoji', 'at': 14, 'emoji': '🎬'}],
                                   'teaser': {'from': 20, 'to': 24}, 'title': 'A moment', 'summary': 'ok'})

            options = {'mode': 'moments', 'translate': 'fr', 'sfx': True, 'fix': False}
            with patch.object(sound, 'effect_samples', return_value={}):
                result = v.edit(source, root / 'out', name='talk.mp4', editor=editor, options=options,
                                transcriber=lambda wav, d: {'language': 'en', 'words': v.clean_words(words, d)})
            self.assertEqual(result['shorts'], 2)
            self.assertEqual(calls.count('images'), 2)                       # each Short reviewed once
            first = result['results'][0]
            self.assertEqual((first['width'], first['height'], first['layout']), (1080, 1920, 'blur'))   # no face: whole picture
            self.assertTrue(first['teaser'] and first['ending'] and first['translated'] == 'fr')
            self.assertEqual(first['graphics'].get('hook'), None)          # G1 (the hook) was dropped by the review
            info = v.probe(root / 'out' / '1' / 'video.mp4')
            self.assertAlmostEqual(info['duration'], first['seconds'], delta=0.1)
            preview = v.probe(root / 'out' / '1' / 'preview.mp4')
            self.assertEqual((preview['width'], preview['height'], preview['audio']), (540, 960, True))
            self.assertAlmostEqual(preview['duration'], info['duration'], delta=0.05)
            self.assertGreater(first['seconds'], 2.4 + 9)                    # the moment, the teaser and the ending
            cover = subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'stream=width,height', '-of', 'csv=p=0',
                                    str(root / 'out' / '1' / 'video.png')], capture_output=True, text=True).stdout.strip()
            self.assertEqual(cover, '720,1280')
            saved = json.loads((root / 'out' / '1' / 'plan.json').read_text())
            self.assertEqual(saved['plan']['hook'], '')
            self.assertEqual(len(saved['words']), 28)
            key = next(g['key'] for g in saved['groups'] if not g['key'].startswith('t'))
            # The owner fixes a caption, removes the title and puts the cut word back.
            with patch.object(sound, 'effect_samples', return_value={}):
                fixed = v.edit(source, root / 'fixed', editor=editor, revise=root / 'out' / '1' / 'plan.json',
                               changes=json.dumps({'captions': {key: 'Owner fixed this'}, 'visuals': {'0': {'remove': True}}, 'restore': [0]}))
            again = fixed['results'][0]
            self.assertEqual(fixed['shorts'], 1)
            self.assertNotIn('title', again['graphics'])
            self.assertIn('Owner fixed this', (root / 'fixed' / '1' / 'captions.srt').read_text())
            self.assertEqual(again['wordsCut'], 0)
            self.assertEqual(again['summary'], 'Fixed by you.')
            self.assertEqual(calls.count('images'), 2)                       # a fix is not reviewed again


if __name__ == '__main__':
    unittest.main()


class SyncTests(unittest.TestCase):
    def test_variable_phone_frame_rates_keep_the_camera_rate(self):
        import json as _json
        from unittest import mock
        stream = {'codec_type': 'video', 'width': 1080, 'height': 1920, 'avg_frame_rate': '30840000/1200199', 'r_frame_rate': '30000/1001'}
        answer = _json.dumps({'streams': [stream, {'codec_type': 'audio'}], 'format': {'duration': '40'}})
        with mock.patch('subprocess.run', return_value=mock.Mock(returncode=0, stdout=answer)):
            self.assertAlmostEqual(v.probe('x.mp4')['fps'], 29.97, places=2)   # not the 25.7 average

    def test_every_piece_starts_at_zero_and_stays_420(self):
        graph = v.piece_filters({'s': 1.0, 'e': 2.5, 'teaser': True, 'flash_in': True}, (1080, 1920), v.Rate(30), 1.5)
        video, audio = graph.split(';[0:a]') if ';[0:a]' in graph else graph.split('[v];')
        self.assertIn('fps=30:start_time=0', graph)
        self.assertIn('trim=end=1.500000,setpts=PTS-STARTPTS', graph)
        self.assertLess(graph.index('fade=t=in'), graph.rindex('format=yuv420p'))   # the white fade cannot leave 4:4:4
        self.assertIn('aresample=48000:async=1:first_pts=0', graph)
        self.assertIn('asetpts=N/SR/TB', graph)

    def test_proofread_lines_across_a_cut_keep_their_words(self):
        words = timed('one two three four five')
        tokens = v.caption_tokens(words, {2}, [(0, 4, 'One, two, four and five.')], 'en')
        self.assertEqual([t['w'].strip() for t in tokens], ['One,', 'two,', 'four', 'and', 'five.'])
        cut = (words[2]['s'], words[2]['e'])
        for token in tokens:                                   # nothing lands in the cut word
            self.assertFalse(cut[0] < (token['s'] + token['e']) / 2 < cut[1])
        self.assertEqual([t['s'] for t in tokens], sorted(t['s'] for t in tokens))

    def test_unsure_words_are_marked_for_the_editor(self):
        words = [{'w': ' clear', 's': 0, 'e': 0.3, 'p': 0.9}, {'w': ' mumbled', 's': 0.4, 'e': 0.7, 'p': 0.2}]
        self.assertEqual(v.transcript_lines(words).splitlines()[1], '1\t0.40\tmumbled (?)')

    def test_extra_proofreading_corrects_only_uncertain_uncovered_words(self):
        words = [{**w, 'p': p} for w, p in zip(timed('clear mumbled finish check'), [0.95, 0.23, 0.92, 0.20])]
        plan = {'captions': [(0, 0, 'Clear')], 'removed': {3}}
        def editor(prompt):
            self.assertIn('1\tmumbled (?)', prompt)
            self.assertNotIn('3\tcheck (?)', prompt)
            return '{"captions":[{"from":1,"text":"mumble"},{"from":2,"text":"invented"},{"from":3,"text":"removed"}]}'
        lines = v.proofread_uncertain(words, plan, 'en', editor)
        self.assertEqual(lines, [(0, 0, 'Clear'), (1, 1, 'mumble')])
        corrected = v.caption_tokens(words, plan['removed'], lines, 'en')[1]
        self.assertEqual((corrected['w'].strip(), corrected['s'], corrected['p']), ('mumble', words[1]['s'], 0.23))

    @unittest.skipUnless(shutil.which('ffmpeg'), 'needs FFmpeg')
    def test_a_phone_video_with_late_sound_and_a_teaser_stays_in_sync(self):
        """A phone's sound track starts after its picture and frames are dropped; with a teaser the
        picture used to fall 0.1 s behind the sound and captions came 0.23 s early."""
        import numpy as np

        def beeps(path):
            x = np.frombuffer(subprocess.run(['ffmpeg', '-v', 'error', '-i', str(path), '-vn', '-ac', '1', '-ar', '8000', '-f', 's16le', '-'],
                                             capture_output=True).stdout, '<i2').astype(float)
            level = np.array([np.sqrt((x[i:i + 80] ** 2).mean()) for i in range(0, len(x) - 80, 80)])
            on = level > level.max() * 0.3
            return [i * 0.01 for i in range(1, len(on)) if on[i] and not on[i - 1]]

        def flashes(path):
            out = subprocess.run(['ffprobe', '-v', 'error', '-f', 'lavfi', '-i', f'movie={path},crop=iw*0.05:ih*0.1:0:ih*0.55,signalstats',
                                  '-show_entries', 'frame=pts_time:frame_tags=lavfi.signalstats.YAVG', '-of', 'json'],
                                 capture_output=True, text=True).stdout
            frames = [(float(f['pts_time']), float(f['tags']['lavfi.signalstats.YAVG'])) for f in json.loads(out)['frames']]
            return [t for (t, y), (_, before) in zip(frames[1:], frames) if y > 128 and before <= 128]

        with tempfile.TemporaryDirectory() as root:
            root = Path(root)
            subprocess.run(['ffmpeg', '-v', 'error', '-y', '-f', 'lavfi', '-i', 'color=black:size=270x480:rate=30000/1001', '-f', 'lavfi',
                            '-i', 'sine=f=600:r=48000', '-filter_complex', "[0:v]drawbox=c=white:t=fill:enable='lt(mod(t,1.7),0.4)',"
                            "select='not(eq(mod(n,7),3))'[v];[1:a]volume='if(lt(mod(t,1.7),0.4),0.6,0)':eval=frame,atrim=start=0.25[a]",
                            '-map', '[v]', '-map', '[a]', '-fps_mode', 'vfr', '-t', '24', '-c:v', 'libx264', '-c:a', 'aac', str(root / 'phone.mp4')], check=True)

            def ear(wav, duration):
                # Like Whisper: words are where the sound is in the extracted speech track.
                found = beeps(wav)
                return {'language': 'en', 'words': v.clean_words([{'w': f' w{n}', 's': t, 'e': t + 0.38, 'p': 1} for n, t in enumerate(found)], duration)}

            def editor(prompt, images=()):
                if images:
                    return '{"drop": [], "cover": 1}'
                count = len(re.findall(r'^\d+\t', prompt.split('Transcript:')[1], re.M))
                return json.dumps({'teaser': {'from': count - 4, 'to': count - 3}, 'title': 'T', 'summary': 's'})

            result = v.edit(root / 'phone.mp4', root / 'out', editor=editor, transcriber=ear,
                            options={'mode': 'one', 'shape': 'original', 'ending': False, 'fix': False})
            self.assertTrue(result['results'][0]['teaser'])
            video = root / 'out' / '1' / 'video.mp4'
            sound, picture = beeps(video), flashes(video)
            self.assertGreaterEqual(len(picture), 8)
            offsets = [min((b - f for b in sound), key=abs) for f in picture[3:]]          # after the teaser and its flash
            self.assertLess(max(abs(o) for o in offsets), 0.06, offsets)
            srt = (root / 'out' / '1' / 'captions.srt').read_text()
            starts = [sum(float(p) * m for p, m in zip(re.split('[:,]', t), (3600, 60, 1, 0.001)))
                      for t in re.findall(r'(\d\d:\d\d:\d\d,\d\d\d) -->', srt)]
            late = [min((b - c for b in sound), key=abs) for c in starts]
            self.assertLess(max(abs(x) for x in late), 0.05, late)                        # captions come with the words
