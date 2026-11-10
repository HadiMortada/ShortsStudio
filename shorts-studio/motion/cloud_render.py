"""Render an approved storyboard on GitHub Actions or locally."""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent / "server"))
from studio_core import validate_plan, probe_video


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--voice", default="af_heart")
    p.add_argument("--style", default="Neon kinetic",
                   choices=["Neon kinetic", "Minimal", "Cyber"])
    p.add_argument("--no-voice", action="store_true", help="Silent CI/demo render")
    p.add_argument("--no-music", action="store_true", help="Disable original synthesized ambience")
    a = p.parse_args()
    raw = json.loads(a.plan.read_text(encoding="utf-8"))
    plan = validate_plan(raw, raw.get("title", ""), int(raw.get("duration_sec",30)))
    exe = ROOT / "node_modules" / ".bin" / "remotion"
    if not exe.exists():
        raise RuntimeError("Run npm install in shorts-studio/motion first.")
    audio = ROOT / "public" / "narration.wav"
    ambience = ROOT / "public" / "ambient.wav"
    audio.parent.mkdir(exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="studio-cloud-") as folder:
        try:
            if a.no_voice:
                length = plan["duration_sec"]
                subprocess.run(["ffmpeg","-y","-loglevel","error","-f","lavfi",
                                "-i","anullsrc=r=24000:cl=mono","-t",str(length),
                                "-c:a","pcm_s16le",str(audio)],check=True,timeout=60)
            else:
                from renderer import synthesize_voice
                length = synthesize_voice(plan["narration"],a.voice,audio)
            if length > 59.5:
                raise ValueError("Narration exceeds the 60-second target")
            duration = min(60.,max(plan["duration_sec"],length+.4))
            if not a.no_music:
                from renderer import synthesize_original_ambience
                synthesize_original_ambience(duration,ambience,a.style)
            props=Path(folder)/"props.json"
            props.write_text(json.dumps({
                "plan":plan,"style":a.style,"durationSec":duration,
                "audioDuration":length,"hasAudio":True,
                "hasMusic":not a.no_music},ensure_ascii=False),
                encoding="utf-8")
            a.out.parent.mkdir(parents=True,exist_ok=True)
            subprocess.run([str(exe),"render","src/index.tsx","StudioShort",
                str(a.out.resolve()),"--props",str(props),
                "--codec","h264","--crf","20","--concurrency","2","--log","error"],
                cwd=ROOT,check=True,timeout=1200)
            metrics=probe_video(a.out)
            a.out.with_suffix(".json").write_text(json.dumps({
                "plan":plan,"metrics":metrics,"style":a.style,
                "voice":a.voice if not a.no_voice else "silent smoke-test"
            },ensure_ascii=False,indent=2),encoding="utf-8")
            print(json.dumps(metrics))
        finally:
            audio.unlink(missing_ok=True)
            ambience.unlink(missing_ok=True)


if __name__ == "__main__":
    main()
