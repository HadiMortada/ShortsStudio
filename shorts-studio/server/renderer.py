"""Kokoro TTS + FFmpeg: animated vertical typography, no stock-media dependency."""
import math
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


def execute(args):
    result = subprocess.run(args, capture_output=True, text=True, check=False, timeout=450)
    if result.returncode:
        raise RuntimeError("FFmpeg failed: " + result.stderr[-650:])


def create_video(plan, voice, style, duration_sec, output):
    if not shutil.which("ffmpeg"):
        raise RuntimeError("Install FFmpeg on your runner.")
    scenes = plan["scenes"][:8]
    with tempfile.TemporaryDirectory(prefix="shorts-") as tmp:
        tmp = Path(tmp)
        wav = tmp / "narration.wav"
        audio_duration = synthesize_voice(plan["narration"], voice, wav)
        total_duration = max(float(duration_sec), audio_duration + .4)
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
                 "-c:v","copy","-c:a","aac","-b:a","160k",
                 "-t",str(total_duration),"-movflags","+faststart",str(output)])
    return output
