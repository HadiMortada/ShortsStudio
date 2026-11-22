import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import numpy as np

import sound

CUES = [{'start': 0, 'end': 3.0, 'text': 'a'}, {'start': 3.25, 'end': 6.0, 'text': 'b'}]
WORDS = [{'start': s, 'end': s + 0.3, 'text': 'w'} for s in np.arange(0.1, 5.6, 0.4).round(2).tolist()]


def rms(track, a, b):
    return float(np.sqrt(np.mean(track[int(a * sound.RATE):int(b * sound.RATE)] ** 2)))


class SoundTests(unittest.TestCase):
    def test_music_ducks_and_scene_changes_have_a_sparse_tonal_cue(self):
        track = sound.bed(9.0, CUES, WORDS, end_card_at=6.8, seed='job')
        self.assertEqual(track.shape, (int(9.0 * sound.RATE), 2))
        self.assertGreater(rms(track, 6.3, 6.7), 2 * rms(track, 1.2, 2.0), 'louder in the pause than while speaking')
        no_cue = sound.bed(9.0, CUES[:1], WORDS, end_card_at=6.8, seed='job')
        self.assertGreater(float(np.max(np.abs(track - no_cue))), 0.001,
                           'a subtle note accompanies the first actual scene change')
        self.assertLess(float(np.abs(track - no_cue).max()), 0.05,
                        'a scene change should never be a noisy, dominant whoosh')
        self.assertLess(float(np.abs(track).max()), 0.35, 'quiet: never louder than the voice')
        self.assertFalse(np.array_equal(track, sound.bed(9.0, CUES, WORDS, 6.8, seed='other')), 'each film sounds its own')

    def test_quiet_topic_has_no_generated_music_or_transition_effects(self):
        self.assertFalse(np.any(sound.bed(9.0, CUES, WORDS, 6.8, seed='job', mood='quiet')))

    def test_only_some_transitions_get_a_cue(self):
        cues = [{'start': i * 1.8, 'end': (i + 1) * 1.8, 'text': 'scene'} for i in range(7)]
        with_cues = sound.bed(15, cues, [], seed='job')
        without = sound.bed(15, [], [], seed='job')
        difference = with_cues - without
        self.assertGreater(np.abs(difference[int(1.7 * sound.RATE):int(2 * sound.RATE)]).max(), 0.001)
        self.assertLess(np.abs(difference[int(3.45 * sound.RATE):int(4 * sound.RATE)]).max(), 0.00001)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'needs ffmpeg')
    def test_the_mix_covers_the_whole_film(self):
        with tempfile.TemporaryDirectory() as root:
            voice = Path(root) / 'narration.mp3'
            subprocess.run(['ffmpeg', '-v', 'error', '-f', 'lavfi', '-i', 'sine=frequency=180:duration=6',
                            '-c:a', 'libmp3lame', str(voice)], check=True)
            self.assertTrue(sound.mix(voice, {'cues': CUES, 'words': WORDS}, 9.0, 'job', end_card_seconds=2.2))
            length = float(subprocess.check_output(['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
                                                    '-of', 'csv=p=0', str(voice)]))
            self.assertAlmostEqual(length, 9.0, delta=0.15)

    def test_a_problem_keeps_the_plain_narration(self):
        with tempfile.TemporaryDirectory() as root:
            voice = Path(root) / 'narration.mp3'
            voice.write_bytes(b'not audio' * 200)
            self.assertFalse(sound.safe_mix(voice, {'cues': CUES, 'words': WORDS}, 9.0))
            self.assertEqual(voice.read_bytes(), b'not audio' * 200)
