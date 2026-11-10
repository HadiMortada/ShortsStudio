"""Kokoro TTS + FFmpeg: animated vertical typography, no stock-media dependency."""
import math
import json
import os
import random
import shutil
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf
from PIL import Image, ImageDraw, ImageFont

W, H = 720, 1280
ACCENTS = {
    "Neon kinetic": ((148, 119, 250), (65, 214, 177)),
    "Minimal": ((225, 215, 190), (152, 174, 195)),
    "Cyber": ((61, 224, 235), (255, 87, 178)),
}


def text_font(size):
    choices = [
        "/usr/share/fonts/truetype/noto/NotoSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation2/LiberationSans-Bold.ttf"
    ]
    for f in choices:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def wrap_text(draw, txt, font, max_width):
    words = txt.split()
    if len(words) == 1:
        words = list(txt) if len(txt) > 20 else words
    lines = []
    line = ""
    for word in words:
        combined = f"{line} {word}".strip() if len(words) < len(txt) else line + word
        if draw.textbbox((0, 0), combined, font=font)[2] <= max_width:
            line = combined
        else:
            if line: lines.append(line)
            line = word
    if line: lines.append(line)
    return lines[:5]


def frame_image(title, subhead, n, total, style, path):
    top, glow = ACCENTS.get(style, ACCENTS["Neon kinetic"])
    img = Image.new("RGB", (W, H))
    pix = img.load()
    for y in range(H):
        p = y / H
        for x in range(W):
            v = (x / W - .5) ** 2 + (p - .48) ** 2
            shine = max(0, 1 - math.sqrt(v) * 1.55)
            pix[x, y] = (
                int(9 + top[0] * .13 * shine + 12 * p),
                int(13 + top[1] * .10 * shine + 7 * p),
                int(29 + top[2] * .20 * shine + 10 * p)
            )
    draw = ImageDraw.Draw(img)
    cx, cy = 380, 580
    for r in (240, 340, 450, 570):
        col = tuple(int(v * .28) for v in glow)
        draw.ellipse((cx-r, cy-r, cx+r, cy+r), outline=col, width=2)
    draw.rounded_rectangle((50, 64, 259, 112), radius=23, fill=(32, 35, 58))
    draw.text((69, 72), "SHORTS  /  STUDIO", font=text_font(17), fill=(211, 218, 247))
    draw.text((54, 235), f"0{n}   /   0{total}", font=text_font(34), fill=top)
    draw.line((54, 305, W-54, 305), fill=top, width=3)
    title = title[:120].strip()
    font = text_font(60 if len(title) < 50 else 49)
    lines = wrap_text(draw, title, font, W - 108)
    line_height = 77 if len(title) < 50 else 65
    y = max(420, 615 - len(lines)*line_height//2)
    for line in lines:
        draw.text((54, y), line, font=font, fill=(248, 248, 255), stroke_width=1)
        y += line_height
    draw.rounded_rectangle((54, 1068, W-54, 1073), radius=2, fill=(48, 49, 74))
    draw.rounded_rectangle((54, 1068, 54 + (W-108)*n/total, 1073), radius=2, fill=top)
    draw.text((54, 1120), subhead[:45], font=text_font(22), fill=(185, 191, 219))
    draw.text((54, 1208), "MOTION  •  VOICE  •  IDEAS", font=text_font(16), fill=(121, 134, 161))
    img.save(path)


def synthesize_voice(narration, voice, dest):
    from kokoro import KPipeline
    pipeline = KPipeline(lang_code=voice[0])
    chunks = []
    for _, _, audio in pipeline(narration, voice=voice, speed=1.0, split_pattern=r"\n+"):
        if audio is not None and len(audio):
            chunks.append(np.asarray(audio, dtype=np.float32))
    if not chunks:
        raise RuntimeError("Kokoro failed to synthesize voice. Check installed model and language.")
    sf.write(dest, np.concatenate(chunks), 24000)
    return len(np.concatenate(chunks)) / 24000.0


def synthesize_original_ambience(seconds, dest, style="Neon kinetic"):
    """Light, original synthesized ambient sound bed; no external music or licensing."""
    sample_rate = 24000
    t = np.arange(int(seconds * sample_rate),dtype=np.float64) / sample_rate
    roots = {"Neon kinetic": 110.0, "Minimal": 98.0, "Cyber": 123.47}
    fundamental = roots.get(style,110.0)
    tones = (np.sin(2*np.pi*fundamental*t)
             + .43*np.sin(2*np.pi*fundamental*1.5*t)
             + .27*np.sin(2*np.pi*fundamental*2*t))
    pulse = .65 + .35 * np.sin(2*np.pi*.48*t)**2
    fade = np.minimum(1.0,np.minimum(t / 1.5,(seconds-t) / 1.5))
    audio = .0075 * tones * pulse * np.maximum(0,fade)
    sf.write(dest,audio.astype(np.float32),sample_rate)
    return dest


def execute(args):
    result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=450)
    if result.returncode:
        raise RuntimeError("FFmpeg failed: " + result.stderr[-650:])


def _classic_video(plan, voice, style, duration_sec, output):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Install FFmpeg on your runner.")
    scenes = [str(s.get("headline","")) if isinstance(s,dict) else str(s) for s in plan["scenes"][:8]]
    with tempfile.TemporaryDirectory(prefix="shorts-") as tmp:
        tmp = Path(tmp)
        wav = tmp / "narration.wav"
        audio_duration = synthesize_voice(plan["narration"], voice, wav)
        total_duration = min(60., max(float(duration_sec), audio_duration + .4))
        segment_duration = total_duration / len(scenes)
        parts = []
        for idx, headline in enumerate(scenes):
            png = tmp / f"scene-{idx:02}.png"
            part = tmp / f"scene-{idx:02}.mp4"
            frame_image(str(headline), plan["title"], idx + 1, len(scenes), style, png)
            # Subtle zoom-in creates continuous movement while titles and cards change.
            vf = ("zoompan=z='min(zoom+0.00038,1.085)':"
                  "x='iw/2-(iw/zoom/2)':y='ih/2-(ih/zoom/2)':"
                  f"d=1:s={W}x{H}:fps=30,"
                  "fade=t=in:st=0:d=0.22,"
                  f"fade=t=out:st={max(0,segment_duration-.22):.3f}:d=0.22,"
                  "format=yuv420p")
            execute(["ffmpeg","-y","-hide_banner","-loglevel","error",
                     "-loop","1","-framerate","30","-i",str(png),
                     "-vf",vf,"-t",str(segment_duration),"-an",
                     "-c:v","libx264","-preset","veryfast","-crf","25",
                     "-pix_fmt","yuv420p",str(part)])
            parts.append(part)
        concat = tmp / "list.txt"
        concat.write_text("".join("file '" + str(p).replace("'", "'\\''") + "'\n" for p in parts), encoding="utf-8")
        joined = tmp / "joined.mp4"
        execute(["ffmpeg","-y","-hide_banner","-loglevel","error",
                 "-f","concat","-safe","0","-i",str(concat),
                 "-c","copy",str(joined)])
        execute(["ffmpeg","-y","-hide_banner","-loglevel","error",
                 "-i",str(joined),"-i",str(wav),
                 "-map","0:v:0","-map","1:a:0",
                 "-af","apad",
                 "-vf","scale=1080:1920:flags=lanczos",
                 "-c:v","libx264","-preset","veryfast","-crf","21",
                 "-c:a","aac","-b:a","160k",
                 "-t",str(total_duration),"-movflags","+faststart",str(output)])
    return output


def create_video(plan, voice, style, duration_sec, output):
    """Use Remotion for premium scenes when installed; otherwise a 1080p classic fallback.

    SHORTS_RENDERER=remotion requires npm install in ../motion.
    SHORTS_RENDERER=classic forces the original FFmpeg implementation.
    In auto mode, prefer Remotion when available.
    """
    renderer = os.getenv("SHORTS_RENDERER", "auto").lower()
    motion_root = Path(__file__).resolve().parent.parent / "motion"
    remotion = motion_root / "node_modules" / ".bin" / "remotion"
    if renderer not in ("classic", "auto", "remotion"):
        raise ValueError("SHORTS_RENDERER must be auto, remotion or classic")
    if renderer == "classic" or (renderer == "auto" and not remotion.exists()):
        return _classic_video(plan, voice, style, duration_sec, output)
    if not remotion.is_file():
        raise RuntimeError("Remotion dependencies missing. Run npm install in shorts-studio/motion.")
    with tempfile.TemporaryDirectory(prefix="shorts-motion-") as tmp:
        folder = Path(tmp)
        wav = folder / "narration.wav"
        audio_duration = synthesize_voice(plan["narration"], voice, wav)
        if audio_duration > 59.5:
            raise ValueError("Narration is longer than 60 seconds; shorten the script.")
        duration = min(60., max(float(duration_sec), audio_duration + .4))
        public = motion_root / "public"
        public.mkdir(parents=True, exist_ok=True)
        audio_target = public / "narration.wav"
        ambience_target = public / "ambient.wav"
        props_path = folder / "props.json"
        shutil.copyfile(wav, audio_target)
        enable_music = os.getenv("SHORTS_AUDIO_BED", "1") == "1"
        if enable_music:
            synthesize_original_ambience(duration, ambience_target, style)
        props_path.write_text(json.dumps({
            "plan": plan,
            "durationSec": duration,
            "audioDuration": audio_duration,
            "hasAudio": True,
            "hasMusic": enable_music,
            "style": style,
        }, ensure_ascii=False), encoding="utf-8")
        try:
            result = subprocess.run([
                str(remotion), "render", "src/index.tsx", "StudioShort",
                str(Path(output).resolve()), "--props", str(props_path),
                "--codec", "h264", "--crf", "20", "--concurrency", "2",
                "--log", "error",
            ], cwd=motion_root, capture_output=True, text=True, timeout=1000, check=False)
            if result.returncode:
                raise RuntimeError("Remotion render failed: " + result.stderr[-1400:])
        finally:
            audio_target.unlink(missing_ok=True)
            ambience_target.unlink(missing_ok=True)
    return output
