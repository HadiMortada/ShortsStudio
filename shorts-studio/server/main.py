"""Self-hosted Shorts Studio personal runner. No Claude tokens are collected."""
from __future__ import annotations

import hmac
import json
import os
import random
import re
import shutil
import sqlite3
import subprocess
import threading
import time
import traceback
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, RedirectResponse
from pydantic import BaseModel, Field

from renderer import create_video

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("STUDIO_DATA", str(ROOT / "data"))).resolve()
DATA.mkdir(parents=True, exist_ok=True)
DB = DATA / "studio.db"
TOKEN = os.getenv("STUDIO_TOKEN", "")
PUBLIC_URL = os.getenv("PUBLIC_BASE_URL", "http://localhost:8787").rstrip("/")
GOOGLE_SECRETS = os.getenv("GOOGLE_CLIENT_SECRETS", "")
YT_TOKEN = DATA / "youtube_token.json"
SCOPES = ["https://www.googleapis.com/auth/youtube.upload"]
YT_STATE = {}
worker_lock = threading.Lock()

VOICES = {
    "a": "af_alloy af_aoede af_bella af_heart af_jessica af_kore af_nicole af_nova af_river af_sarah af_sky am_adam am_echo am_eric am_fenrir am_liam am_michael am_onyx am_puck am_santa",
    "b": "bf_alice bf_emma bf_isabella bf_lily bm_daniel bm_fable bm_george bm_lewis",
    "e": "ef_dora em_alex em_santa",
    "f": "ff_siwis",
    "h": "hf_alpha hf_beta hm_omega hm_psi",
    "i": "if_sara im_nicola",
    "j": "jf_alpha jf_gongitsune jf_nezumi jf_tebukuro jm_kumo",
    "p": "pf_dora pm_alex pm_santa",
    "z": "zf_xiaobei zf_xiaoni zf_xiaoxiao zf_xiaoyi zm_yunjian zm_yunxi zm_yunxia zm_yunyang",
}
LANGUAGES = {"a":"American English","b":"British English","e":"Spanish","f":"French",
             "h":"Hindi","i":"Italian","j":"Japanese","p":"Brazilian Portuguese","z":"Mandarin Chinese"}

app = FastAPI(title="Shorts Studio Runner", docs_url=None, redoc_url=None)


def check_auth(authorization: str | None = Header(default=None)):
    if not TOKEN:
        raise HTTPException(503, "STUDIO_TOKEN is not configured")
    if not authorization or not hmac.compare_digest(authorization, "Bearer " + TOKEN):
        raise HTTPException(401, "Invalid runner access token")


def connect():
    conn = sqlite3.connect(DB, timeout=20)
    conn.row_factory = sqlite3.Row
    return conn


def initialize():
    with connect() as db:
        db.execute("""CREATE TABLE IF NOT EXISTS jobs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            topic TEXT NOT NULL,
            lang TEXT NOT NULL,
            voice TEXT NOT NULL,
            style TEXT NOT NULL,
            duration_sec INTEGER NOT NULL,
            auto_upload INTEGER NOT NULL,
            repeat_daily INTEGER NOT NULL,
            local_time TEXT NOT NULL,
            timezone TEXT NOT NULL,
            next_run TEXT NOT NULL,
            status TEXT NOT NULL,
            video_path TEXT NOT NULL DEFAULT '',
            youtube_url TEXT NOT NULL DEFAULT '',
            error TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL
        )""")


initialize()


class JobCreate(BaseModel):
    topic: str = Field(min_length=8, max_length=500)
    lang: str = "a"
    voice: str = "random"
    style: str = "Neon kinetic"
    duration_sec: int = Field(default=30, ge=15, le=60)
    auto_upload: bool = False
    repeat_daily: bool = False
    time: str = "18:00"
    timezone: str = "UTC"


def next_daily(local_time: str, zone: str):
    try:
        hour, minute = [int(x) for x in local_time.split(":")]
        if not 0 <= hour < 24 or not 0 <= minute < 60:
            raise ValueError()
        tz = ZoneInfo(zone)
    except (ValueError, KeyError):
        raise HTTPException(422, "Invalid time or timezone")
    now = datetime.now(tz)
    due = now.replace(hour=hour, minute=minute, second=0, microsecond=0)
    if due <= now:
        due += timedelta(days=1)
    return due.astimezone(timezone.utc).isoformat()


@app.get("/health", dependencies=[Depends(check_auth)])
def health():
    return {"ok": True, "claude_installed": shutil.which("claude") is not None,
            "ffmpeg_installed": shutil.which("ffmpeg") is not None,
            "youtube_connected": YT_TOKEN.exists(), "voices": sum(len(v.split()) for v in VOICES.values())}


@app.post("/jobs", dependencies=[Depends(check_auth)])
def create_job(body: JobCreate):
    if body.lang not in VOICES:
        raise HTTPException(422, "Unsupported language")
    if body.voice != "random" and body.voice not in VOICES[body.lang].split():
        raise HTTPException(422, "Voice does not match language")
    if body.style not in ("Neon kinetic", "Minimal", "Cyber"):
        raise HTTPException(422, "Unsupported style")
    if not re.fullmatch(r"([01][0-9]|2[0-3]):[0-5][0-9]", body.time):
        raise HTTPException(422, "Time must be HH:mm")
    run_at = next_daily(body.time, body.timezone) if body.repeat_daily else datetime.now(timezone.utc).isoformat()
    with connect() as db:
        cur = db.execute("""INSERT INTO jobs
           (topic,lang,voice,style,duration_sec,auto_upload,repeat_daily,local_time,timezone,next_run,status,created_at)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
           (body.topic.strip(), body.lang, body.voice, body.style, body.duration_sec,
            int(body.auto_upload), int(body.repeat_daily), body.time, body.timezone, run_at,
            "scheduled" if body.repeat_daily else "queued", datetime.now(timezone.utc).isoformat()))
        return {"id": cur.lastrowid, "status": "scheduled" if body.repeat_daily else "queued"}


@app.get("/jobs", dependencies=[Depends(check_auth)])
def list_jobs():
    with connect() as db:
        records = db.execute("SELECT * FROM jobs ORDER BY id DESC LIMIT 100").fetchall()
    return {"jobs": [{
        "id": r["id"], "topic": r["topic"], "voice": r["voice"], "status": r["status"],
        "error": r["error"], "next_run": r["next_run"] if r["repeat_daily"] else "",
        "has_video": bool(r["video_path"] and Path(r["video_path"]).exists()),
        "youtube_url": r["youtube_url"], "created_at": r["created_at"]
    } for r in records]}


@app.get("/videos/{job_id}", dependencies=[Depends(check_auth)])
def video_preview(job_id: int):
    with connect() as db:
        row = db.execute("SELECT video_path FROM jobs WHERE id=?", (job_id,)).fetchone()
    if row is None or not row["video_path"] or not Path(row["video_path"]).is_file():
        raise HTTPException(404, "Video not found")
    return FileResponse(row["video_path"], media_type="video/mp4", filename=f"short-{job_id}.mp4")


def claude_plan(row):
    language = LANGUAGES[row["lang"]]
    prompt = f"""Return ONLY valid JSON, with fields "title" (max 90 chars), "narration" (one continuous script),
"description" (YouTube description max 500 chars), "scenes" (array of 5 to 8 short kinetic on-screen
headlines, each max 45 characters). Create a factual, exciting vertical video, about {row["duration_sec"]}
seconds of narration. Write all text in {language}. No emojis in headlines. Avoid invented factual claims.
Topic from viewer: {row["topic"]}
Do not run tools or change files; only produce the JSON plan. No markdown fences."""
    output = subprocess.run(
        ["claude", "-p", prompt, "--output-format", "text"],
        capture_output=True, text=True, timeout=240, check=False, cwd=DATA,
        env={k:v for k,v in os.environ.items() if k != "ANTHROPIC_API_KEY"}
    )
    if output.returncode != 0:
        raise RuntimeError("Claude Code failed. Sign into the official CLI on the runner, and check your plan/limits. " + output.stderr[-250:])
    text = output.stdout.strip()
    if text.startswith("```"):
        text = re.sub(r"^\x60{3}(?:json)?\s*|\s*\x60{3}$", "", text).strip()
    plan = json.loads(text)
    if not isinstance(plan.get("scenes"), list) or len(plan["scenes"]) < 2 or not plan.get("narration"):
        raise ValueError("Claude returned an incomplete plan")
    plan["title"] = str(plan.get("title") or row["topic"])[:90]
    plan["description"] = str(plan.get("description") or "")[:500]
    plan["scenes"] = [str(s)[:75] for s in plan["scenes"][:8]]
    plan["narration"] = str(plan["narration"])[:1800]
    return plan


def youtube_credentials():
    if not YT_TOKEN.exists():
        raise RuntimeError("Connect YouTube in the app before enabling auto-publish.")
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    creds = Credentials.from_authorized_user_file(str(YT_TOKEN), SCOPES)
    if creds.expired and creds.refresh_token:
        creds.refresh(Request())
        YT_TOKEN.write_text(creds.to_json(), encoding="utf-8")
        os.chmod(YT_TOKEN, 0o600)
    if not creds.valid:
        raise RuntimeError("YouTube authorization expired. Reconnect your channel.")
    return creds


def upload_to_youtube(mp4: Path, plan):
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload
    youtube = build("youtube", "v3", credentials=youtube_credentials(), cache_discovery=False)
    request = youtube.videos().insert(
        part="snippet,status",
        body={
            "snippet": {
                "title": plan["title"][:100],
                "description": (plan.get("description", "") + "\n\n#Shorts")[:4900],
                "categoryId": "27"
            },
            "status": {
                "privacyStatus": "public",
                "selfDeclaredMadeForKids": False
            }
        },
        media_body=MediaFileUpload(str(mp4), mimetype="video/mp4", resumable=True)
    )
    result = None
    while result is None:
        _, result = request.next_chunk()
    return "https://www.youtube.com/shorts/" + result["id"]


def execute_job(job_id):
    try:
        with connect() as db:
            row = db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone()
        voice = row["voice"]
        if voice == "random":
            voice = random.choice(VOICES[row["lang"]].split())
        plan = claude_plan(row)
        path = DATA / f"video-{job_id}-{int(time.time())}.mp4"
        create_video(plan, voice, row["style"], row["duration_sec"], path)
        with connect() as db:
            db.execute("UPDATE jobs SET video_path=?,voice=?,status='ready',error='' WHERE id=?",
                       (str(path), voice, job_id))
        url = ""
        if row["auto_upload"]:
            url = upload_to_youtube(path, plan)
        with connect() as db:
            db.execute("UPDATE jobs SET status=?,youtube_url=? WHERE id=?",
                       ("posted" if url else "ready", url, job_id))
    except Exception as exc:
        traceback.print_exc()
        with connect() as db:
            db.execute("UPDATE jobs SET status=?,error=? WHERE id=?", ("error", str(exc)[:500], job_id))
    finally:
        with connect() as db:
            r = db.execute("SELECT repeat_daily,local_time,timezone FROM jobs WHERE id=?", (job_id,)).fetchone()
            if r and r["repeat_daily"]:
                due = next_daily(r["local_time"], r["timezone"])
                db.execute("UPDATE jobs SET next_run=? WHERE id=?", (due, job_id))
                # On errors, retain the error status; the next scheduled run will still retry.
        worker_lock.release()


def scheduler_loop():
    while True:
        try:
            if worker_lock.acquire(blocking=False):
                with connect() as db:
                    row = db.execute(
                        "SELECT id FROM jobs WHERE (status IN ('queued','scheduled') OR repeat_daily=1)"
                        " AND next_run<=? ORDER BY next_run,id LIMIT 1",
                        (datetime.now(timezone.utc).isoformat(),)
                    ).fetchone()
                    if row:
                        db.execute("UPDATE jobs SET status='running',error='' WHERE id=?", (row["id"],))
                if row:
                    threading.Thread(target=execute_job, args=(row["id"],), daemon=True).start()
                else:
                    worker_lock.release()
        except Exception:
            traceback.print_exc()
            if worker_lock.locked():
                try: worker_lock.release()
                except RuntimeError: pass
        time.sleep(8)


@app.get("/auth/youtube/start", dependencies=[Depends(check_auth)])
def youtube_start():
    if not GOOGLE_SECRETS or not Path(GOOGLE_SECRETS).exists():
        raise HTTPException(503, "Set GOOGLE_CLIENT_SECRETS to your Google OAuth client JSON on the runner.")
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(GOOGLE_SECRETS, scopes=SCOPES,
        redirect_uri=PUBLIC_URL + "/auth/youtube/callback")
    url, state = flow.authorization_url(access_type="offline", prompt="consent")
    YT_STATE[state] = time.time() + 600
    return {"url": url}


@app.get("/auth/youtube/callback")
def youtube_callback(request: Request, state: str = "", code: str = ""):
    expiration = YT_STATE.pop(state, 0)
    if not code or expiration < time.time():
        raise HTTPException(400, "OAuth state expired or mismatched.")
    from google_auth_oauthlib.flow import Flow
    flow = Flow.from_client_secrets_file(GOOGLE_SECRETS, scopes=SCOPES, state=state,
        redirect_uri=PUBLIC_URL + "/auth/youtube/callback")
    flow.fetch_token(authorization_response=str(request.url))
    YT_TOKEN.write_text(flow.credentials.to_json(), encoding="utf-8")
    os.chmod(YT_TOKEN, 0o600)
    return {"connected": True, "message": "YouTube authorized. Return to Shorts Studio."}


@app.on_event("startup")
def start_scheduler():
    if not TOKEN:
        print("WARNING: Configure STUDIO_TOKEN before accessing the API.")
    threading.Thread(target=scheduler_loop, daemon=True, name="shorts-scheduler").start()
