"""Shared, dependency-light validation and quality checks for Shorts Studio."""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
from pathlib import Path

ALLOWED_STYLES = ("Neon kinetic", "Minimal", "Cyber")
ALLOWED_PRIVACY = ("private", "unlisted", "public")


def validate_plan(value: dict, topic: str = "", duration: int = 30) -> dict:
    """Accept old headline lists and new storyboard objects; produce predictable JSON."""
    if not isinstance(value, dict):
        raise ValueError("Video plan must be a JSON object")
    narration = str(value.get("narration") or "").strip()
    if not 1 <= len(narration) <= 2200:
        raise ValueError("Narration must be 1–2200 characters")
    raw_scenes = value.get("scenes")
    if not isinstance(raw_scenes, list) or not 2 <= len(raw_scenes) <= 8:
        raise ValueError("Storyboard must contain 2–8 scenes")
    scenes = []
    for item in raw_scenes:
        if isinstance(item, str):
            item = {"headline": item}
        if not isinstance(item, dict):
            raise ValueError("Each scene must be an object or text")
        headline = str(item.get("headline") or item.get("title") or "").strip()[:85]
        if not headline:
            raise ValueError("Scene is missing a headline")
        scenes.append({
            "headline": headline,
            "kicker": str(item.get("kicker") or "")[:55],
            "visual": str(item.get("visual") or "orbits")[:35],
            "visual_direction": str(item.get("visual_direction") or "")[:350],
            "accent": str(item.get("accent") or "")[:20],
        })
    return {
        "title": str(value.get("title") or topic or "Shorts Studio")[:90].strip(),
        "description": str(value.get("description") or "")[:500],
        "narration": narration,
        "scenes": scenes,
        "duration_sec": max(15, min(60, int(duration))),
    }


def plan_fingerprint(plan: dict) -> str:
    canonical = json.dumps(plan, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def probe_video(path: str | Path, *, expected_width: int = 1080, expected_height: int = 1920) -> dict:
    """Fail closed if missing, truncated, landscape, silent, or beyond Shorts duration."""
    file = Path(path)
    if not file.is_file() or file.stat().st_size < 20_000:
        raise ValueError("Rendered MP4 is missing or unexpectedly small")
    completed = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries",
         "stream=codec_type,width,height:format=duration", "-of", "json", str(file)],
        capture_output=True, text=True, timeout=35, check=True,
    )
    info = json.loads(completed.stdout)
    streams = info.get("streams") or []
    videos = [s for s in streams if s.get("codec_type") == "video"]
    audios = [s for s in streams if s.get("codec_type") == "audio"]
    if not videos or not audios:
        raise ValueError("MP4 must contain both video and narration audio")
    if (videos[0].get("width"), videos[0].get("height")) != (expected_width, expected_height):
        raise ValueError("Video must be exactly 1080 × 1920 (9:16)")
    duration = float(info.get("format", {}).get("duration") or 0)
    if not 14.5 <= duration <= 62.0:
        raise ValueError("Video must be approximately 15–60 seconds")
    return {"width": expected_width, "height": expected_height,
            "duration": round(duration, 2), "bytes": file.stat().st_size}


def safe_filename(raw: str) -> str:
    return re.sub(r"[^a-zA-Z0-9_-]+", "-", raw).strip("-")[:80] or "short"
