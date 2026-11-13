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
    def test_music_dips_under_the_voice_and_scenes_get_a_whoosh(self):
        track = sound.bed(9.0, CUES, WORDS, end_card_at=6.8, seed='job')
        self.assertEqual(track.shape, (int(9.0 * sound.RATE), 2))
        self.assertGreater(rms(track, 6.3, 6.7), 2 * rms(track, 1.2, 2.0), 'louder in the pause than while speaking')
        self.assertGreater(rms(track, 2.85, 3.1), 2 * rms(track, 2.2, 2.5), 'a whoosh before the second scene')
        self.assertLess(float(np.abs(track).max()), 0.35, 'quiet: never louder than the voice')
        self.assertFalse(np.array_equal(track, sound.bed(9.0, CUES, WORDS, 6.8, seed='other')), 'each film sounds its own')

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
