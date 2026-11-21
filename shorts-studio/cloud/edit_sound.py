"""Sound work for edited videos.

- Who speaks when (speaker diarization) with sherpa-onnx: pyannote segmentation plus a speaker
  embedding model, all on the CPU, no account or token. When two or more people really speak, each
  gets a caption colour and Claude sees who said what.
- Optional, quiet sound effects from Kenney's CC0 Interface Sounds library. Tonal fallbacks keep
  effects available when a download fails.
"""
import hashlib
import io
import sys
import wave
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from urllib.request import Request, urlopen

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

EFFECT_SOURCE = 'https://kenney.nl/assets/interface-sounds'
EFFECT_MIRROR = ('https://raw.githubusercontent.com/Calinou/kenney-interface-sounds/'
                 '4596a49eaf5a533948d49a47467f606bcdea70ff/addons/kenney_interface_sounds/')
EFFECT_FILES = {
    'transition': ('select_005.wav', 'e0f14f9ca1fbc0fb8dc49bc5133747d86f15c9d79a0c8bcb7973d7526d092841'),
    'accent': ('select_002.wav', '9f15b5f93716a3ff8842cff73ef932e81beac2d19972066f690154c1f9322f2b'),
    'ending': ('confirmation_001.wav', 'f9d0ef5a5c740c2ac250ac3876db9e4340669044a5bf78ae09192918db3e2ebc'),
}
EFFECT_MAX_BYTES = 128_000


def effect_samples(opener=urlopen):
    """Download one small, pinned sample for each cue. Keep any samples that load successfully."""
    import numpy as np

    def download(item):
        name, (file, expected) = item
        try:
            with opener(Request(EFFECT_MIRROR + file, headers={'User-Agent': 'ShortsStudio/1.0'}), timeout=6) as response:
                data = response.read(EFFECT_MAX_BYTES + 1)
            if len(data) > EFFECT_MAX_BYTES or hashlib.sha256(data).hexdigest() != expected:
                raise ValueError('Unexpected sound file')
            with wave.open(io.BytesIO(data), 'rb') as sample:
                rate, tracks, length = sample.getframerate(), sample.getnchannels(), sample.getnframes()
                if sample.getsampwidth() != 2 or tracks not in (1, 2) or not 8000 <= rate <= 96000 or not 0 < length <= rate:
                    raise ValueError('Unsupported sound format')
                mono = np.frombuffer(sample.readframes(length), '<i2').reshape(-1, tracks).mean(axis=1) / 32768
            audio = np.interp(np.arange(round(length * RATE / rate)) * (rate / RATE), np.arange(length), mono)
            fade = min(round(RATE * 0.012), len(audio) // 2)
            if fade:
                audio[:fade] *= np.linspace(0, 1, fade)
                audio[-fade:] *= np.linspace(1, 0, fade)
            return name, (audio / max(0.015, np.abs(audio).max())).astype(np.float32)
        except (OSError, ValueError, EOFError, wave.Error) as error:
            print(f'Sound effect {name} unavailable: {str(error)[:140]}', file=sys.stderr)
            return name, None

    with ThreadPoolExecutor(max_workers=3) as pool:
        return {name: sample for name, sample in pool.map(download, EFFECT_FILES.items()) if sample is not None}


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


SOUNDS = {'transition': (pop, 0.15), 'accent': (pop, 0.11), 'ending': (shimmer, 0.12)}
SOUND_FOR = {'picture': 'transition', 'teaser': 'transition', 'title': 'accent', 'point': 'accent',
             'emoji': 'accent', 'endcard': 'ending'}


def sfx_track(events, total, path, samples=None):
    """Mix sparse, quiet cues aligned to the graphics and transitions."""
    import numpy as np
    track = np.zeros(int(total * RATE), np.float32)
    samples = samples or {}
    cache = {}
    last = -float('inf')
    for at, kind in sorted(events):
        name = SOUND_FOR.get(kind)
        if not name or not 0 <= at < total or (at - last < 0.85 and name != 'ending'):
            continue
        if name not in cache:
            maker, gain = SOUNDS[name]
            cache[name] = (samples[name] if name in samples else maker()) * gain
        sound = cache[name]
        start = int(at * RATE)
        end = min(len(track), start + len(sound))
        if end > start:
            track[start:end] += sound[:end - start]
            last = at
    track = np.clip(track, -1, 1)
    stereo = (np.repeat(track[:, None], 2, axis=1) * 32767).astype('<i2')
    with wave.open(str(path), 'wb') as out:
        out.setnchannels(2)
        out.setsampwidth(2)
        out.setframerate(RATE)
        out.writeframes(stereo.tobytes())
    return path


def events_for(overlays, teaser_end=None):
    events = [(o['start'], o['type']) for o in overlays if o['type'] in SOUND_FOR]
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
