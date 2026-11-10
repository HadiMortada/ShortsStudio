# ShortsStudio — private AI Shorts production studio

Android remote controller + official Claude Code CLI planner + free Kokoro voice + Remotion motion graphics + FFmpeg fallback + optional cloud rendering.

## Implemented

- Android APK: Jetpack Compose; create one-off/daily jobs, choose topic, style, voice, YouTube visibility, pause/resume, inspect history and pipeline counts. Access token is encrypted with Android Keystore.
- Python worker: FastAPI + SQLite queue, recovery on restart, historical renders, seven-video media retention, publishing receipts, basic analytics.
- Claude: the official CLI is called on the owner's signed-in machine. Built-in and MCP tools are explicitly denied for each untrusted topic. Login credentials are never collected.
- Motion: 1080x1920 / 30fps Remotion with kinetic headlines, abstract geometry, original synthesized ambient audio (disable with SHORTS_AUDIO_BED=0) and approximate narration captions. The original Python/FFmpeg renderer remains as fallback.
- Speech: Kokoro-82M voice generation with existing presets across nine language groups. Arabic is not currently supported by these voice presets.
- YouTube: OAuth upload, private/unlisted/public visibility, automatic upload off by default, private visibility default. Interrupted/uncertain uploads require manual review.
- GitHub Actions: Android APK build, Python and TypeScript CI, optional approved-plan cloud daily render.

## Set up the self-hosted runner

Requirements: Linux, Python 3.11+, Node 22+, FFmpeg, espeak-ng, Noto/DejaVu fonts, official Claude Code CLI, 4–8 GB RAM recommended.

    sudo apt-get install -y ffmpeg espeak-ng fonts-dejavu-core fonts-noto-core python3-venv
    cd shorts-studio/motion
    npm install
    cd ../server
    python3 -m venv .venv
    source .venv/bin/activate
    pip install -r requirements.txt
    claude auth login
    claude -p 'Say hello' --tools '' --disallowedTools '*'
    export STUDIO_TOKEN="$(openssl rand -hex 24)"
    export PUBLIC_BASE_URL="https://YOUR-RUNNER-HOST"
    export SHORTS_RENDERER=remotion
    uvicorn main:app --host 127.0.0.1 --port 8787

Put a secured HTTPS reverse proxy in front of the service. Never expose the development server to the public internet. The Android application now refuses non-HTTPS remote hosts, preventing bearer tokens being transmitted over plain HTTP.

Render modes: SHORTS_RENDERER=remotion requires Node dependencies; auto uses Remotion when installed and otherwise FFmpeg; classic forces FFmpeg. Classic mode upscales to 1080x1920.

The runner must remain online for daily Claude Pro planning. Claude Pro usage limits still apply and it does NOT provide unlimited API credits; do not repurpose subscription authentication into a public SaaS.

## YouTube connection

1. Enable YouTube Data API v3 and set up Google OAuth consent and a web client.
2. Keep the client JSON outside source control and set GOOGLE_CLIENT_SECRETS to its absolute path.
3. Configure redirect URI https://YOUR-RUNNER-HOST/auth/youtube/callback, then choose Connect YouTube in Android Settings.
4. Auto-upload and public visibility require deliberate choices. Start with private videos.
5. If a run reports needs_review, check YouTube Studio before retrying. YouTube inserts have no caller-supplied idempotency key.

## Daily GitHub cloud rendering

Cloud render is for APPROVED storyboards, not arbitrary AI-generated claims. Commit a validated JSON plan named shorts-studio/plans/YYYY-MM-DD.json. At 18:00 UTC the scheduled workflow processes today's file if present, and otherwise skips. Manually run the "Shorts Studio approved daily video" workflow with the example plan for an initial test.

Publishing is disabled by default. To opt in, set YOUTUBE_OAUTH_JSON as a GitHub Actions secret containing a valid authorized_user OAuth refresh-token JSON, and SHORTS_AUTOPUBLISH=true as a repository variable. SHORTS_PRIVACY can be private, unlisted or public (default private). A receipt in shorts-studio/ledger is committed BEFORE upload and blocks automatic reattempts. An unknown upload outcome requires human reconciliation.

Claude Pro sessions are not magically available in GitHub Actions. The cloud workflow only renders pre-approved plans. For novel AI-created scripts every day, keep the owner-signed-in official Claude CLI on the self-hosted runner, or arrange another approved integration with its own billing/terms. GitHub Actions in private repos uses an included monthly allowance and can be billable beyond it.

## Testing and APK

    PYTHONPATH=shorts-studio/server python3 -m unittest discover -s shorts-studio/server/tests -v
    python3 -m compileall -q shorts-studio/server shorts-studio/motion
    cd shorts-studio/motion && npm install && npx tsc --noEmit
    cd ../ && gradle :app:assembleDebug

The APK workflow is at .github/workflows/shorts-studio-apk.yml and its artifact is ShortsStudio-debug-apk.

## Known limitations

- Real phone, Claude login, Kokoro inference and YouTube channel have not been tested end-to-end in this environment.
- Captions approximate voice timing; true per-word alignment requires an additional speech alignment model and compute.
- This is polished programmatic 2D/2.5D motion design, not realistic cinematic AI-generated moving footage.
- Analytics show local renders/posts, not YouTube views or retention; the YouTube Analytics API needs separate OAuth authorization.
- Arabic TTS, user-editable timelines and sound-effect libraries are not yet included.
- Cron timing on GitHub Actions may drift under load.
- Remotion is free for qualifying individuals/small teams; consult the current license for larger organizations.
