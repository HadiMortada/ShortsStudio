# Free mode: GitHub Actions with Claude sign-in (no Modal, no VPS)

The workflow **Shorts Studio Claude film** (`.github/workflows/shorts-studio-claude.yml`) runs the
same film pipeline as the cloud service on GitHub's own runners. Claude Code signs in with your
Claude subscription token, so there is no API key, no Modal account and no server to rent.
Claude writes the original Remotion animation, edge-tts records the narration and Remotion
renders a 1080×1920 H.264/AAC MP4 at 30 fps. Failed renders get one repair, and six sampled
frames are reviewed before any automatic upload.

### One-time setup

1. On a computer with Claude Code installed and signed in to your Claude Pro/Max plan, run
   `claude setup-token` and copy the token it prints.
2. In GitHub: **Settings → Secrets and variables → Actions → New repository secret**, name
   `CLAUDE_CODE_OAUTH_TOKEN`, paste the token. Never commit it or paste it into a brief.
3. Merge this workflow into the default branch. GitHub only lists manually run workflows, and
   only runs schedules, from the default branch.

### Make a film

**Actions → Shorts Studio Claude film → Run workflow**, enter a brief, length, narrator and
whether Claude may research the web. A film usually takes 10–20 minutes. Download the MP4 from
the run's **Artifacts** (kept 14 days); the GitHub mobile app works for this too. The run summary
shows the title and visual-review result.

### Optional: YouTube upload

Add a `YOUTUBE_OAUTH_JSON` secret: authorized-user JSON with `client_id`, `client_secret` and a
`refresh_token` for the `youtube.upload` scope. This is the same secret the approved-storyboard
workflow uses. Tick **publish** when running. The upload happens in a separate job that never
sees the Claude token. It is skipped if the visual review fails. Each run writes a receipt to
`shorts-studio/ledger/<job>.json` before uploading, so re-running a job never posts twice.

### Optional: daily autopilot

Edit `shorts-studio/autopilot/settings.json`: set `"enabled": true` and a `domain` (plus
audience, language, tone, days, timezone, length, narrator, publish, privacy). The schedule
runs daily at 15:23 UTC; change the `cron` line in the workflow to pick another time. Claude
searches the web for a fresh, evidenced topic in your domain and avoids topics already in
`shorts-studio/autopilot/history.json`, which the workflow commits after each film. A manual
run with an empty brief uses these settings even while `enabled` is false.

### Safety

- Claude Code receives only its own token. It never gets the GitHub token or YouTube credentials.
- The generated TSX renders in a separate process with no secrets. That process runs in a
  network namespace where only loopback is reachable (`cloud/isolate.py`), like the Modal sandbox.
- Checkout credentials are not persisted. Only the deliver job can write to the repository.

### Costs and limits

- **GitHub Actions:** free and unlimited on public repositories. Private repositories get
  GitHub's monthly free Linux minutes, roughly 10–20 minutes per film.
- **Claude:** usage counts against your subscription's limits.
- **Narration:** edge-tts uses Microsoft's free online voices without a key. It depends on
  that service being available.
- **Runner restrictions:** if your GitHub account restricts hosted runners (see the APK note
  below), this workflow cannot start either.
- **Not available in this mode:** reference-folder uploads and the Android Director console.
  Both stay on the Modal service.

Validation: 61 cloud tests pass, including 17 for this mode, and actionlint finds no problems
in the workflow. A full local run (sample design, test tone instead of edge-tts, which was
unreachable in the development sandbox) rendered a valid 15-second 1080×1920 H.264/AAC film
inside the network-isolated render process. A live run on GitHub with a real Claude token
has not been performed yet.

# Shorts Studio Director 0.6.0

The Android Create tab now opens the paired Director console directly, including optional
research, references and cloud daily settings. Accounts opens the dedicated `/setup` page
for Claude authorization. `GET /productions` lists retained cloud films, including jobs
created from the web console; Library shows their state and can save completed MP4s on the
phone. Tapping the Director MP4 download also uses the native authenticated download and
adds the file to the phone Library. Existing local films remain listed, and repeated saves
are deduplicated. The web console includes the same cloud film history and keeps unfinished
job IDs in local storage across WebView recreation.

The cloud fix is deployed with the existing access-secret reference and pairing key.
The delivered `director` build (versionCode 7, `com.shortsstudio.app.hd.director`) installs
alongside HD as **Shorts Studio Director**. Its dedicated `shortsstudiodirector` link is
available as **Connect Director APK** on `/setup`. The server's existing Claude login is
shared. Do not uninstall HD: its existing phone videos and encrypted pairing stay intact.
The old HD signing key was unavailable, so this is intentionally a separate application.

Build Director with `gradle :app:testDirectorUnitTest :app:assembleDirector`. Restore its
retained private key and set `STUDIO_SIGNING_KEYSTORE` before making subsequent compatible
Director updates. Keep that key outside Git and restore it from the private build-signing
backup. The normal HD build remains available for builds with its original signing key.

Validation: all 44 cloud tests and all 9 Android unit tests passed; the Director APK built
and passed signature verification. Live `/setup`, `/studio` and authenticated
`GET /productions` were checked after deployment; the user's previously missing film is
ready and downloadable. Unauthenticated Library access returns 401. The recovered file is
1080×1920 H.264 at 30 fps with AAC audio. Browser visual QA was attempted but Chromium could
not open a socket under the current sandbox. Real-device installation, file selection and
WebView download behavior remain to be confirmed on Android.

# Shorts Studio 0.5.0

## Director upgrade (deployed; live film verification in progress)

The new authenticated `/studio` console supports on-demand creation, optional web research,
reference folders, cloud daily topic selection and opt-in YouTube uploads. The existing HD APK
keeps working with `/productions`; no replacement APK is needed to use the console in Chrome.
Open the existing private setup link, then **Open full Studio** after deploying this revision.

- Claude can choose WebSearch/WebFetch when useful. `research=off` disables both. Uploaded
  reference text and local media manifests are supplied as context without filesystem tools.
- Upload PDF, DOCX, UTF-8 text, images, clips, or ZIP/folder bundles. Bounds: 100 files,
  100 MB expanded per folder, 35 MB per file, five selected folders, 180,000 text characters
  per film. Truncation is marked. Scanned PDFs require extracted text; OCR is not included.
- External media requests currently support verified reusable Wikimedia Commons images and
  video. Original illustrations replace unavailable media. Credits remain with the film.
  Arbitrary stock sites and private social-media accounts are not connected.
- The director writes bespoke TSX with richer motion instructions and a larger code budget.
  Six rendered stills and the source are reviewed by Claude; significant defects get one repair.
  Failed/unavailable review holds automatic upload. This assesses sampled visuals and source,
  not continuous motion or audio quality, and does not guarantee professional results.
- Cloud daily creation checks settings every five minutes, handles IANA timezones and selected
  weekdays, searches for evidence of interest, remembers 90 topics, and claims each local day
  once. A serialized dispatcher handles concurrent phone and scheduler starts. Daily creation
  is disabled until configured. A saved time already passed starts on the next selected day.
- Configure a Google OAuth **Web application** client with the exact server
  `/youtube/callback` redirect URI, connect through Google, then select private/unlisted/public.
  Authorization stays outside Claude prompts. Unknown upload outcomes are not retried.
  YouTube API project restrictions and OAuth testing-mode expiry may require Google setup.

Deploy the complete `cloud/` directory, not just `modal_bridge.py`. Preserve the existing
48-character pairing key using `STUDIO_DEPLOY_KEY`; otherwise phones must pair again.
Required deployment access is a valid Modal profile in an environment that can connect to
Modal's API. The upgraded service was deployed on 11 November 2026 using the existing access-secret reference and a rolling update. The endpoint and HD APK pairing were preserved.

Validation on 11 November 2026: 41 cloud tests passed; Python and JavaScript syntax checks
passed. A local Remotion fixture bundled successfully, but full rendering and browser launch
were blocked by sandbox socket restrictions. Live film verification is in progress. Google consent and actual YouTube uploading require connecting the user's OAuth client and channel before testing.

The legacy APK background schedule and approved-storyboard GitHub workflow remain separate.
Use the new cloud console for autonomous topic selection and daily publishing.

Describe a film and tap **Create with Claude**. Opus 5.5 writes a complete, original React/Remotion animation for the brief: custom SVG illustrations, typography, masks, camera moves, easing and transitions. The service records neural narration and renders a 1080×1920 H.264/AAC MP4 at 30 fps. The Android app downloads that finished film to its Library. The main creation flow no longer uses the phone's TTS engine or the restricted 432×768 vector timeline.

## Narration

Choose automatic language selection or a neural narrator in English or Arabic. Available choices include Andrew, Ava, Guy, Jenny, Ryan, Sonia, Rami, Layla, Hamed and Zariyah. The `edge-tts` project provides access to Microsoft's online neural voices without a speech API key. It requires internet access and availability of that service. Speech errors are reported; there is no silent fallback to Android TTS. Narration keeps its natural pace; the animation can extend slightly to accommodate the recording, up to 75 seconds.

## Rendering

Claude supplies actual TSX animation code, not a list of headings or a fixed decoration template. A separate, bounded Modal Sandbox compiles and renders that code. It has no network access, secrets or access to the Claude credential Volume. The render image includes pinned Remotion 4.0.421 packages, Chromium, FFmpeg and local English/Arabic fonts. Narration is added outside the generated component. Failed renders are sent back to Claude for one code repair; recorded narration is preserved.

A dedicated Modal worker owns each production, so narration and rendering remain active independently of HTTP polling or app closure. Creation uses a job ID with authenticated status polling and download endpoints. The app can resume polling the same job after a connection interruption or reopening the same request, without deliberately starting another generation. Downloads use a temporary file and only become visible after completion. Five recent cloud projects are retained; downloaded phone videos remain in the Library until deleted. Interrupted local runs are marked failed on restart; cloud job status is retained separately and stale jobs report an interruption.

## Install/update

Version code 6, package `com.shortsstudio.app.hd`, app name **Shorts Studio HD**. The new HD app installs alongside the earlier app, preserving its saved videos. Pair the new app using **Connect HD APK** on your existing private setup page. The older app retains its connection and Library. Claude's cloud sign-in is shared and does not need to be repeated if it is still active.

The cloud's **Connect older APK** link remains available for previous app versions. They continue to use the earlier `/direct` timeline endpoint. The APK workflow is retained, but account-level runner restrictions can prevent GitHub-hosted builds before compilation starts; an independently built APK must pass the same Android tests.

## Cloud setup

From a checkout of this repository, install Modal and run:

```bash
cd shorts-studio/cloud
modal run modal_bridge.py
```

This builds the isolated rendering image and deploys `shorts-studio-cloud`. Open the complete private setup link it prints, including `#key=…`, tap **Connect HD APK**, then complete official Claude login if needed. Keep the private setup link private. Passwords stay on Claude's official authorization site. Official CLI credentials are stored in the private `shorts-studio-claude-home` Volume.

For an existing deployment, the maintainer can supply its existing 48-character hexadecimal connection key in `STUDIO_DEPLOY_KEY` to preserve pairing. Never commit that key or a Modal token. Without that variable, setup generates a new key and phones need to pair again. The `cloud/production.py` module and `cloud/render/` directory must accompany `modal_bridge.py`; copying only the bridge into Playground is no longer sufficient for this version.

Modal compute consumes the account's hosting credits. Claude usage counts against the signed-in subscription. No paid speech API is configured. Do not assume unlimited free cloud rendering. Only one film is accepted at a time. Cloud creation can continue if the app closes. Reopen the same request to resume waiting and downloading; Android can delay background jobs. Existing daily director schedules use the new cloud film pipeline after this APK update. Older legacy schedules retain their previous local behavior.

## Verification

```bash
python -m pip install 'modal>=1.6,<2' 'fastapi[standard]>=0.115,<1' httpx
python -m unittest discover -s shorts-studio/cloud -v
```

Android SDK 35, Java 17, Gradle 8.10.2:

```bash
cd shorts-studio
gradle :app:testDebugUnitTest :app:assembleDebug
```

The delivered 0.5.0 HD build passed all 9 Android unit tests, all 27 cloud tests and APK signature verification on 10 November 2026. A live dedicated cloud job generated a 15-second film with Claude-authored Remotion source, Andrew neural narration, 1080×1920 H.264 video at 30 fps and AAC audio; its finished file was downloaded through the authenticated app endpoint.

The APK workflow runs cloud and Android checks. Cloud tests cover code validation, neural voice selection, render repair, natural speech timing, interrupted jobs, job spawning independent of web shutdown and sandbox isolation. HTTP tests cover authenticated polling, bounded result redirects and rejecting incomplete/non-video downloads. Live verification should additionally create a narrated MP4 and inspect its audio/video tracks and rendered frames.

The older `server/`, `motion/`, Android `MobileVideo` and `/direct` implementation remain for compatibility with old installations; the new creation flow uses `cloud/production.py` and `cloud/render/render.mjs`.


