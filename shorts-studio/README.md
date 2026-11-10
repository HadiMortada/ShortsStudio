# Shorts Studio — Android + self-hosted AI video runner

An Android-first, vertical-video automation controller: describe a topic, choose kinetic motion style, choose any of 54 Kokoro voices (or a random voice *within the selected language*), generate 15–60s videos, schedule daily production, and optionally publish to YouTube Shorts.

## What works

- Native Android app built with Kotlin + Jetpack Compose, persistent runner settings, dashboard, queue, video preview, scheduling and connection controls.
- Self-hosted Python FastAPI worker with SQLite durable queue, daily time-zone aware scheduler, bounded tasks, and a one-job-at-a-time worker.
- Claude Code official unmodified CLI creates a JSON plan for the narration, scenes and title.
- Open-weight Kokoro-82M generates speech with **54 voice presets across 9 language groups**. Model weights are downloaded on first use and aren't included in the APK.
- Pillow + FFmpeg create animated 9:16 kinetic-typography videos with subtle camera movement and scene transitions.
- Google OAuth 2.0 authorizes YouTube upload on the runner; upload is optional and defaults to OFF.
- GitHub Actions compiles the debug APK and publishes it as a workflow artifact.

## Key constraint: Claude Code authentication

**This is NOT a third-party Claude sign-in interface.** It does not issue, intercept, collect or reuse Claude.ai OAuth tokens. On your own machine or VPS, install the official Claude Code CLI and run:

```bash
claude auth login
claude -p 'Say hello'
```

Approve the sign-in in Anthropic's own browser flow. The runner subsequently calls the **unmodified** `claude -p` binary installed and authenticated on that machine. Anthropic may restrict third-party integrations against subscription limits; check their current terms and authorization for your use case. Building a general-purpose product for others requires approved commercial/API authentication. **This project does not promise that a subscription can legally power a third-party SaaS.**

Docs: https://code.claude.com/docs/en/authentication

## Runner setup (Linux)

Requirements: Python 3.11+, FFmpeg with libx264, DejaVu/Noto fonts, working official Claude Code CLI, online runner with enough CPU/RAM/disk for Kokoro and FFmpeg. Recommended 4–8 GB RAM.

```bash
sudo apt-get install -y ffmpeg espeak-ng fonts-dejavu-core fonts-noto-core python3-venv
cd shorts-studio/server
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Invent your own long random secret:
export STUDIO_TOKEN="$(openssl rand -hex 24)"
export PUBLIC_BASE_URL="https://YOUR-HTTPS-RUNNER.example"
uvicorn main:app --host 127.0.0.1 --port 8787
```

Place a secure HTTPS reverse proxy in front of the runner. Do not expose the development server or credential files publicly. HTTPS is required for a remote phone. Use the same runner URL and STUDIO_TOKEN in Android > Settings. If running only on a trusted LAN, use `http://192.168.x.y:8787` and bind Uvicorn to `0.0.0.0` deliberately; plaintext LAN connections are not confidential.

**Background automation requires an always-on runner.** Turning off the phone is fine; turning off the runner pauses the jobs. On restart, missed due jobs are picked up. No automation happens in GitHub Actions.

## YouTube publishing (optional)

1. Create a Google Cloud project with YouTube Data API v3 enabled and configure OAuth consent.
2. Create an OAuth **Web Application** client. Add the exact redirect URI `https://YOUR-HTTPS-RUNNER.example/auth/youtube/callback`.
3. Download the JSON credentials to the runner, outside source control:
   `export GOOGLE_CLIENT_SECRETS="/secure/path/client_secret.json"`
4. Set `PUBLIC_BASE_URL` to the registered HTTPS origin, restart the runner, and open **Settings → Connect YouTube securely** on Android.
5. Google grants only the `youtube.upload` scope; refresh credentials stay in the runner's private data folder. Never commit credentials.

The app uploads portrait MP4s; final eligibility and classification as a YouTube Short is determined by YouTube. Publishing is OFF by default. Set auto-publish on a job only once you have checked the channel's policies and enabled uploads.

## Android APK

The GitHub Actions workflow `.github/workflows/shorts-studio-apk.yml` builds a debug APK, available from the workflow run's **ShortsStudio-debug-apk** artifact.

For local Android Studio builds, open `shorts-studio/` and run `gradle :app:assembleDebug` with JDK 17, Android SDK 35 and Gradle 8.10.2.

## Security notes / limitations

This is an **early personal-use prototype**. No multi-user accounts or cloud hosting are bundled. Secure TLS and firewall rules are your responsibility. Store `STUDIO_TOKEN` and OAuth files privately. Runner token is stored in Android app private SharedPreferences; future production versions should use Android Keystore-backed encryption and certificate pinning. YouTube uploads can fail due to quota, authorization, or policy. Voice preview samples and timeline editing are not included. Some non-Latin scripts require appropriate Noto fonts and may not be shaped correctly by basic Pillow drawing. The renderer produces typographic motion videos rather than AI-generated cinematic clips. Always preview and fact-check before publishing.

Repository files live under `shorts-studio/`, isolated from the parent test repository.
