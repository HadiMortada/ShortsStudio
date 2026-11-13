"""Sound design for every film: a quiet music bed that dips while the narrator speaks, a soft whoosh
at each scene change, and gentle pops when the Like and Subscribe buttons are tapped in the ending.

Everything is synthesised here (no samples, so nothing can be claimed for copyright). Each film gets
its own key and chord pattern from its job id. The narration stays the loudest thing at all times;
if anything goes wrong the film simply keeps its plain narration.
"""
import hashlib
import subprocess
import sys
from pathlib import Path

RATE = 44100
PROGRESSIONS = ([0, 7, 9, 5], [9, 5, 0, 7], [0, 9, 5, 7], [5, 0, 7, 9], [0, 5, 9, 7])  # I-V-vi-IV and friends
KEYS = (48, 50, 51, 53, 55, 57)  # C, D, Eb, F, G, A (MIDI of the bass note)
END_TAPS = (26 / 30, 44 / 30)  # the end card's Like and Subscribe taps, seconds after it starts


def _note(midi):
    return 440.0 * 2 ** ((midi - 69) / 12)


def _speech_mask(np, words, length, rate=RATE):
    """1 where the narrator is speaking (words merged across short gaps), smoothed for ducking."""
    mask = np.zeros(length, dtype=np.float32)
    spans = []
    for w in sorted(words, key=lambda w: w['start']):
        if spans and w['start'] - spans[-1][1] < 0.35:
            spans[-1][1] = max(spans[-1][1], w['end'])
        else:
            spans.append([w['start'], w['end']])
    for start, end in spans:
        mask[max(0, int((start - 0.08) * rate)):min(length, int((end + 0.25) * rate))] = 1.0
    # Smooth the edges (about 120 ms) so the music glides down and back up.
    width = int(0.12 * rate)
    kernel = np.ones(width, dtype=np.float32) / width
    return np.clip(np.convolve(mask, kernel, mode='same'), 0, 1)


def bed(duration, cues, words, end_card_at=None, seed='film'):
    """The sound bed (stereo float32, RATE Hz) for a film of `duration` seconds."""
    import numpy as np
    rng = np.random.default_rng(int(hashlib.sha256(seed.encode()).hexdigest()[:8], 16))
    length = int(duration * RATE)
    t = np.arange(length, dtype=np.float32) / RATE
    out = np.zeros((length, 2), dtype=np.float32)
    key = int(rng.choice(KEYS))
    steps = PROGRESSIONS[int(rng.integers(len(PROGRESSIONS)))]
    bar = 60 / float(rng.integers(84, 104)) * 4

    # Music: soft pads with a slow swell, a low root note and a quiet plucked arpeggio.
    for index in range(int(duration / bar) + 1):
        start = int(index * bar * RATE)
        if start >= length:
            break
        n = min(int(bar * 1.08 * RATE), length - start)
        seg = t[:n]
        root = key + steps[index % 4]
        minor = steps[index % 4] in (9, 4)
        chord = [root + 12, root + 12 + (3 if minor else 4), root + 19, root + 24]
        env = np.minimum(1, seg / 0.6) * np.minimum(1, (n / RATE - seg) / 0.5)
        pad = sum(np.sin(2 * np.pi * _note(m) * seg + 0.4 * np.sin(2 * np.pi * 0.25 * seg)) for m in chord)
        pad = (pad / len(chord)) * env * 0.5
        bass = np.sin(2 * np.pi * _note(root) * seg) * env * 0.35
        out[start:start + n, 0] += pad * 0.9 + bass
        out[start:start + n, 1] += pad + bass
        for k in range(8):
            a = start + int(k * bar / 8 * RATE)
            m = min(int(0.6 * RATE), length - a)
            if m <= 0:
                continue
            s = t[:m]
            pluck = np.sin(2 * np.pi * _note(chord[[0, 1, 2, 3, 2, 1, 3, 2][k]] + 12) * s) * np.exp(-s * 7) * 0.16
            pan = 0.5 + 0.35 * np.sin(k)
            out[a:a + m, 0] += pluck * (1 - pan)
            out[a:a + m, 1] += pluck * pan
    peak = np.abs(out).max() or 1
    out *= 0.11 / peak  # quiet: well under the narration
    # Duck under the voice: down to about a third while words are spoken.
    out *= (1 - 0.66 * _speech_mask(np, words, length))[:, None]

    # A soft whoosh just before each new scene.
    for cue in cues[1:]:
        centre = cue['start'] - 0.12
        a, m = int((centre - 0.3) * RATE), int(0.5 * RATE)
        if a < 0 or a + m > length:
            continue
        noise = rng.standard_normal(m).astype(np.float32)
        smooth = np.convolve(noise, np.ones(18) / 18, mode='same')
        bright = noise - np.convolve(noise, np.ones(4) / 4, mode='same')
        sweep = np.linspace(0, 1, m)
        whoosh = (smooth * (1 - sweep) + bright * 0.35 * sweep) * np.sin(np.pi * sweep) ** 2 * 0.22
        out[a:a + m, 0] += whoosh * (1 - sweep * 0.6)
        out[a:a + m, 1] += whoosh * (0.4 + sweep * 0.6)

    # The ending: a gentle chime when the card appears, and a pop for each tap.
    if end_card_at is not None:
        for offset, freq, level in [(0.0, 1318.5, 0.07), (0.05, 1975.5, 0.05)] + [(tap, 880.0, 0.12) for tap in END_TAPS]:
            a = int((end_card_at + offset) * RATE)
            m = min(int(0.35 * RATE), length - a)
            if a < 0 or m <= 0:
                continue
            s = t[:m]
            tone = np.sin(2 * np.pi * freq * s * (1 + 0.6 * np.exp(-s * 40))) * np.exp(-s * (12 if offset in END_TAPS else 6)) * level
            out[a:a + m] += tone[:, None]
    # Fade in and out.
    fade_in, fade_out = int(0.4 * RATE), int(1.2 * RATE)
    out[:fade_in] *= np.linspace(0, 1, min(fade_in, length))[:, None]
    out[-fade_out:] *= np.linspace(1, 0, min(fade_out, length))[:, None]
    return out


def mix(audio, design, duration, seed='film', end_card_seconds=0):
    """Mix the sound bed under the narration file (replaced in place, padded to the film length)."""
    import numpy as np
    audio = Path(audio)
    if not audio.is_file() or audio.stat().st_size < 1000:
        return False
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-i', str(audio), '-f', 'f32le', '-ac', '2', '-ar', str(RATE), '-'],
                         check=True, capture_output=True, timeout=120).stdout
    voice = np.frombuffer(raw, dtype=np.float32).reshape(-1, 2)
    length = int(duration * RATE)
    track = np.zeros((length, 2), dtype=np.float32)
    track[:min(length, len(voice))] = voice[:length]
    end_at = duration - end_card_seconds if end_card_seconds else None
    track += bed(duration, design.get('cues') or [], design.get('words') or [], end_at, seed)
    peak = float(np.abs(track).max() or 1)
    if peak > 0.98:
        track *= 0.98 / peak
    out = audio.with_name(audio.stem + '.mixed.mp3')
    subprocess.run(['ffmpeg', '-y', '-v', 'error', '-f', 'f32le', '-ac', '2', '-ar', str(RATE), '-i', '-',
                    '-c:a', 'libmp3lame', '-b:a', '160k', str(out)], input=track.tobytes(), check=True, timeout=120)
    out.replace(audio)
    return True


def safe_mix(audio, design, duration, seed='film', end_card_seconds=0):
    """mix(), but a problem only means the film keeps its plain narration."""
    try:
        return mix(audio, design, duration, seed, end_card_seconds)
    except Exception as error:
        print('Sound design skipped:', type(error).__name__, str(error)[:200], file=sys.stderr, flush=True)
        return False
