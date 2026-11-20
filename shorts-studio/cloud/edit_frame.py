"""Picture work for edited videos.

- Following the speaker: faces are found (YuNet, OpenCV) on frames sampled twice a second. A wide
  video made vertical is cropped around the speaker, each piece of the edit framed on its own (like a
  camera operator re-framing between cuts) and long pieces re-framed when the speaker moves. Punch-in
  zooms centre on the face. With no face to follow (a screen recording, scenery) a wide video is
  shown whole over a blurred, zoomed copy of itself.
- Picture fix: exposure, contrast and colour are measured on sampled frames and gently corrected;
  shake is measured with vid.stab and the video is stabilised only when it is actually shaky.
"""
import math
import re
import subprocess
import sys
from pathlib import Path

SAMPLE_FPS = 2.0
ANALYSIS_SIDE = 640


def analysis_size(info):
    width, height = info['width'], info['height']
    scale = ANALYSIS_SIDE / max(width, height)
    return max(2, round(width * scale / 2) * 2), max(2, round(height * scale / 2) * 2)


def sample_frames(source, info, fps=SAMPLE_FPS):
    """Yield (time, BGR frame) at FPS frames a second, downscaled for analysis."""
    import numpy as np
    width, height = analysis_size(info)
    process = subprocess.Popen(['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-i', str(source), '-an',
                                '-vf', f'fps={fps},scale={width}:{height}', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'],
                               stdout=subprocess.PIPE)
    size, n = width * height * 3, 0
    try:
        while True:
            data = process.stdout.read(size)
            if len(data) < size:
                break
            yield n / fps, np.frombuffer(data, np.uint8).reshape(height, width, 3)
            n += 1
    finally:
        process.stdout.close()
        process.kill()
        process.wait()


# ---------------------------------------------------------------------------------------------
# Faces

def face_track(source, info, model, frames=None):
    """[(time, (centre x, centre y, height) as fractions of the frame) or None], the largest face
    in each sampled frame: the person speaking to the camera."""
    import cv2
    width, height = analysis_size(info)
    detector = cv2.FaceDetectorYN.create(str(model), '', (width, height), 0.7, 0.3, 5000)
    track = []
    for t, frame in (frames if frames is not None else sample_frames(source, info)):
        _, faces = detector.detect(frame)
        if faces is None or not len(faces):
            track.append((t, None))
            continue
        x, y, w, h = max(faces, key=lambda f: f[2] * f[3])[:4]
        track.append((t, ((x + w / 2) / width, (y + h / 2) / height, h / height)))
    found = sum(1 for _, face in track if face)
    print(f'Faces found in {found} of {len(track)} sampled frames', flush=True)
    return track


def median(values):
    values = sorted(values)
    if not values:
        return None
    middle = len(values) // 2
    return values[middle] if len(values) % 2 else (values[middle - 1] + values[middle]) / 2


def faces_between(track, start, end, reach=2.0):
    """Faces seen during [start, end]; if none, the nearest ones within REACH seconds."""
    inside = [face for t, face in track if start <= t <= end and face]
    if inside:
        return inside
    near = sorted(((min(abs(t - start), abs(t - end)), face) for t, face in track if face), key=lambda x: x[0])
    return [near[0][1]] if near and near[0][0] <= reach else []


def layout_for(info, size, track):
    """'crop' (cut out a frame of the output's shape), or 'blur' when a wide video becomes vertical
    with no face to follow."""
    width, height = info['width'], info['height']
    wide_to_tall = width / height > 1.2 and size[0] / size[1] < 0.9
    if not wide_to_tall:
        return 'crop'
    seen = sum(1 for _, face in track if face) / max(1, len(track))
    return 'crop' if track and seen >= 0.4 else 'blur'


def crop_box(info, size, zoom, focus, layout='crop'):
    """(x, y, w, h) in source pixels: the output's shape, zoomed, centred on the face when known."""
    width, height = info['width'], info['height']
    if layout == 'blur':
        aspect = width / height                      # the whole wide picture is shown, zoom only
    else:
        aspect = size[0] / size[1]
    if width / height > aspect:
        w, h = height * aspect, height
    else:
        w, h = width, width / aspect
    w, h = w / zoom, h / zoom
    if focus:
        cx, cy = focus[0] * width, focus[1] * height + h * 0.12   # the face a little above centre
    else:
        cx, cy = width / 2, (height - h) * 0.42 + h / 2
    x = min(max(0.0, cx - w / 2), width - w)
    y = min(max(0.0, cy - h / 2), height - h)
    even = lambda v: int(v) // 2 * 2
    return even(x), even(y), max(2, even(w)), max(2, even(h))


def frame_pieces(pieces, track, info, size, fps, layout):
    """Each piece gets its crop; a long piece is split where the speaker moves well out of frame."""
    out = []
    width = info['width']
    crop_w = crop_box(info, size, 1.0, None, layout)[2] / width
    for piece in pieces:
        start, end = piece['s'], piece['e']
        segments = [(start, end)]
        if layout == 'crop' and end - start > 6 and crop_w < 0.95:
            # Windows of ~3 s: a new framing where the face's median position moves more than a
            # quarter of the crop's width.
            bounds, windows, at = [], [], start
            while at < end - 1e-6:
                windows.append((at, min(end, at + 3.0)))
                at += 3.0
            current = None
            for a, b in windows:
                faces = [f for t, f in track if a <= t <= b and f]
                position = median([f[0] for f in faces]) if faces else None
                if position is None:
                    continue
                if current is not None and abs(position - current) > crop_w / 4 and a - (bounds[-1] if bounds else start) >= 2.5:
                    bounds.append(math.floor(a * fps + 1e-6) / fps)
                    current = position
                elif current is None:
                    current = position
            edges = [start, *[b for b in bounds if start + 1 < b < end - 1], end]
            segments = list(zip(edges, edges[1:]))
        for a, b in segments:
            faces = faces_between(track, a, b)
            focus = (median([f[0] for f in faces]), median([f[1] for f in faces])) if faces else None
            box = crop_box(info, size, piece.get('zoom', 1.0), focus, layout)
            out.append({**piece, 's': round(a, 6), 'e': round(b, 6), 'crop': box, 'layout': layout, 'face': focus is not None})
    return out


# ---------------------------------------------------------------------------------------------
# Picture fix

def picture_fix(frames):
    """A gentle eq filter (gamma, contrast, saturation) from sampled frames, or '' when the picture
    is already fine."""
    import numpy as np
    lumas, spreads, sats = [], [], []
    for n, (_, frame) in enumerate(frames):
        if n % 4:
            continue                                  # every second second is plenty
        rgb = frame[:, :, ::-1].astype(np.float32) / 255
        luma = rgb @ np.array([0.299, 0.587, 0.114], np.float32)
        lumas.append(float(luma.mean()))
        spreads.append(float(luma.std()))
        sats.append(float((rgb.max(axis=2) - rgb.min(axis=2)).mean()))
        if len(lumas) >= 60:
            break
    if not lumas:
        return ''
    luma, spread, sat = median(lumas), median(spreads), median(sats)
    # FFmpeg's eq maps v to v ** (1 / gamma): above 1 brightens. Move the median towards mid grey.
    gamma = 1.0
    if luma < 0.40:
        gamma = min(1.45, math.log(max(luma, 0.08)) / math.log(0.46))
    elif luma > 0.68:
        gamma = max(0.85, math.log(luma) / math.log(0.58))
    contrast = 1.08 if spread < 0.16 else 1.03
    saturation = 1.14 if sat < 0.16 else 1.06
    return 'eq=' + (f'gamma={gamma:.2f}:' if abs(gamma - 1) >= 0.04 else '') + f'contrast={contrast:.2f}:saturation={saturation:.2f}'


LOCAL_MOTION = re.compile(r'\(LM (-?\d+) (-?\d+) ')


def shake(trf_text, window=15):
    """Jitter in pixels (at the analysis width) from a vid.stab transforms file: the frame-to-frame
    camera motion minus its smooth (intended) part."""
    motions = []
    for line in trf_text.splitlines():
        if not line.startswith('Frame '):
            continue
        vectors = [(int(x), int(y)) for x, y in LOCAL_MOTION.findall(line)]
        if len(vectors) >= 3:
            motions.append((median([v[0] for v in vectors]), median([v[1] for v in vectors])))
    if len(motions) < window * 2:
        return 0.0
    jitter = []
    for n in range(len(motions)):
        around = motions[max(0, n - window // 2): n + window // 2 + 1]
        mx, my = sum(m[0] for m in around) / len(around), sum(m[1] for m in around) / len(around)
        jitter.append(math.hypot(motions[n][0] - mx, motions[n][1] - my))
    return sum(jitter) / len(jitter)


def is_shaky(source, workdir, info, limit=1.1):
    """Measure shake at 480 px wide on at most 90 s of the video."""
    trf = Path(workdir) / 'shake.trf'
    width = 480 if info['width'] >= info['height'] else 272
    done = subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-v', 'error', '-t', '90', '-i', str(Path(source).resolve()), '-an',
                           '-vf', f'scale={width}:-2,vidstabdetect=shakiness=6:accuracy=9:result={trf.name}', '-f', 'null', '-'],
                          capture_output=True, text=True, cwd=workdir, timeout=900)
    if done.returncode or not trf.is_file():
        return False, 0.0
    amount = shake(trf.read_text(errors='replace'))
    print(f'Camera shake: {amount:.2f} px', flush=True)
    return amount > limit, amount


def stabilise(source, workdir, info):
    """A steadied copy of the whole video (two vid.stab passes, a light crop hides the moving edges)."""
    workdir = Path(workdir).resolve()
    source = str(Path(source).resolve())
    subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-y', '-v', 'error', '-i', source, '-an',
                    '-vf', 'vidstabdetect=shakiness=6:accuracy=12:result=steady.trf', '-f', 'null', '-'],
                   check=True, cwd=workdir, timeout=7200)
    out = workdir / 'steady.mp4'
    subprocess.run(['ffmpeg', '-hide_banner', '-nostdin', '-y', '-v', 'error', '-i', source,
                    '-vf', 'vidstabtransform=input=steady.trf:smoothing=18:optzoom=1:zoomspeed=0.2:interpol=bicubic,unsharp=5:5:0.5',
                    '-c:v', 'libx264', '-preset', 'veryfast', '-crf', '14', '-pix_fmt', 'yuv420p', '-c:a', 'copy',
                    '-movflags', '+faststart', str(out)], check=True, cwd=workdir, timeout=7200)
    return out


def analyse(source, info, *, faces_model=None, fix=True, need_faces=True):
    """One pass over sampled frames for the faces and the colour fix. Returns {'track', 'eq'}.
    Shake is checked (and fixed) before this, so faces are found on the steadied picture."""
    use_faces = need_faces and faces_model and Path(faces_model).is_file()
    frames = list(sample_frames(source, info)) if (fix or use_faces) else []
    track, eq = [], ''
    if use_faces:
        try:
            track = face_track(source, info, faces_model, frames)
        except Exception as error:   # no face following is no reason to stop
            print('Face tracking skipped:', str(error)[:200], file=sys.stderr)
    if fix:
        try:
            eq = picture_fix(frames)
        except Exception as error:
            print('Colour check skipped:', str(error)[:200], file=sys.stderr)
    return {'track': track, 'eq': eq}
