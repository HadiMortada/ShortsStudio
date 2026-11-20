"""Sound work for edited videos.

- Who speaks when (speaker diarization) with sherpa-onnx: pyannote segmentation plus a speaker
  embedding model, all on the CPU, no account or token. When two or more people really speak, each
  gets a caption colour and Claude sees who said what.
- Sound effects, made here from noise and tones (no music, no samples): a soft whoosh when a title,
  card or photo comes in, a light pop for emoji, a shimmer for the end card. Off unless the owner turns
  them on.
"""
import math
import sys
import wave
from pathlib import Path

RATE = 48000


# ---------------------------------------------------------------------------------------------
# Speakers

def diarize(samples, models, rate=16000):
    """[(start, end, speaker)] for 16 kHz mono float samples. MODELS holds segmentation.onnx and
    embedding.onnx. Returns [] when the models are missing."""
    models = Path(models or '')
    segmentation, embedding = models / 'segmentation.onnx', models / 'embedding.onnx'
    if not segmentation.is_file() or not embedding.is_file():
        return []
    import sherpa_onnx
    config = sherpa_onnx.OfflineSpeakerDiarizationConfig(
        segmentation=sherpa_onnx.OfflineSpeakerSegmentationModelConfig(
            pyannote=sherpa_onnx.OfflineSpeakerSegmentationPyannoteModelConfig(model=str(segmentation)), num_threads=4),
        embedding=sherpa_onnx.SpeakerEmbeddingExtractorConfig(model=str(embedding), num_threads=4),
        clustering=sherpa_onnx.FastClusteringConfig(num_clusters=-1, threshold=0.6),
        min_duration_on=0.3, min_duration_off=0.5)
    if not config.validate():
        raise RuntimeError('The speaker models could not be loaded')
    engine = sherpa_onnx.OfflineSpeakerDiarization(config)
    if engine.sample_rate != rate:
        raise RuntimeError('Unexpected sample rate for speaker detection')
    return [(float(r.start), float(r.end), int(r.speaker)) for r in engine.process(samples).sort_by_start_time()]


def assign_speakers(words, turns, minimum=0.12):
    """A speaker number per word (0 = the main speaker), or [] when only one person really speaks.
    A 'speaker' that holds under MINIMUM of the words, or fewer than 6 of them, is treated as noise
    and folded into its neighbours."""
    if not words or not turns:
        return []
    labels = []
    for word in words:
        middle = (word['s'] + word['e']) / 2
        best, overlap = None, 0.0
        for start, end, speaker in turns:
            shared = min(end, word['e']) - max(start, word['s'])
            if start <= middle <= end and shared >= overlap:
                best, overlap = speaker, shared
        if best is None:                                      # between turns: the nearest one
            best = min(turns, key=lambda t: min(abs(t[0] - middle), abs(t[1] - middle)))[2]
        labels.append(best)
    counts = {}
    for label in labels:
        counts[label] = counts.get(label, 0) + 1
    real = [label for label, n in counts.items() if n >= max(6, minimum * len(words))]
    if len(real) < 2:
        return []
    # Numbered by who speaks most; minor labels take the previous word's speaker.
    order = {label: n for n, label in enumerate(sorted(real, key=lambda l: -counts[l]))}
    out, last = [], order[max(real, key=lambda l: counts[l])]
    for label in labels:
        last = order.get(label, last)
        out.append(last)
    return out


# ---------------------------------------------------------------------------------------------
# Sound effects

def whoosh(seconds=0.42, seed=1):
    import numpy as np
    n = int(seconds * RATE)
    noise = np.random.default_rng(seed).standard_normal(n).astype(np.float32)
    # A moving band-pass: a one-pole low-pass whose cut-off sweeps up then down.
    t = np.linspace(0, 1, n, dtype=np.float32)
    cutoff = 300 + 5200 * np.clip(np.sin(np.pi * t), 0, 1) ** 1.5
    alpha = 1 - np.exp(-2 * np.pi * cutoff / RATE)
    out, low = np.empty(n, np.float32), 0.0
    for i in range(n):
        low += alpha[i] * (noise[i] - low)
        out[i] = low
    out -= np.convolve(out, np.ones(64, np.float32) / 64, mode='same')   # take out the rumble
    envelope = np.clip(np.sin(np.pi * t), 0, 1) ** 2 * (1 - 0.4 * t)
    out *= envelope
    return out / (np.abs(out).max() + 1e-9)


def pop(seconds=0.09):
    import numpy as np
    t = np.arange(int(seconds * RATE), dtype=np.float32) / RATE
    freq = 950 - 4200 * t
    phase = 2 * np.pi * np.cumsum(freq) / RATE
    return (np.sin(phase) * np.exp(-t * 48)).astype(np.float32)


def shimmer(seconds=0.9):
    import numpy as np
    t = np.arange(int(seconds * RATE), dtype=np.float32) / RATE
    tones = sum(np.sin(2 * np.pi * f * t + k) for k, f in enumerate((1318.5, 1760.0, 2637.0)))
    return (tones / 3 * np.exp(-t * 4.5) * np.minimum(1, t / 0.01)).astype(np.float32)


SOUNDS = {'whoosh': (whoosh, 0.22), 'pop': (pop, 0.28), 'shimmer': (shimmer, 0.12)}
SOUND_FOR = {'hook': 'whoosh', 'title': 'whoosh', 'quote': 'whoosh', 'point': 'whoosh', 'picture': 'whoosh',
             'emoji': 'pop', 'endcard': 'shimmer', 'teaser': 'whoosh'}


def sfx_track(events, total, path):
    """A stereo 48 kHz WAV of TOTAL seconds with a soft effect at each (time, kind) event; the
    whoosh peaks just as the graphic lands."""
    import numpy as np
    track = np.zeros(int(total * RATE) + RATE, np.float32)
    cache = {}
    for at, kind in events:
        name = SOUND_FOR.get(kind)
        if not name:
            continue
        if name not in cache:
            maker, gain = SOUNDS[name]
            cache[name] = maker() * gain
        sound = cache[name]
        start = int(max(0.0, at - (0.16 if name == 'whoosh' else 0.0)) * RATE)
        end = min(len(track), start + len(sound))
        if end > start:
            track[start:end] += sound[:end - start]
    track = np.clip(track[:int(total * RATE)], -1, 1)
    stereo = (np.repeat(track[:, None], 2, axis=1) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(stereo.tobytes())
    return path


def events_for(overlays, teaser_end=None):
    events = [(o['start'], o['type']) for o in overlays]
    if teaser_end:
        events.append((teaser_end, 'teaser'))
    return sorted(events)


def report(turns, labels):
    if labels:
        print(f'Speakers: {max(labels) + 1} people speak', flush=True)
    elif turns:
        print('Speakers: one main speaker', flush=True)
    else:
        print('Speakers: not checked', file=sys.stderr, flush=True)
