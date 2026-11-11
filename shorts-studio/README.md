# Free mode: phone page + GitHub Actions + Claude subscription (no Modal, no VPS)

Open your private Shorts Studio page on any phone, type a brief and tap **Create film**:

1. **Cloudflare (free)** hosts the page (`shorts-studio/web/`). It is password protected and
   starts films, shows progress, plays finished films, and edits autopilot settings.
2. **GitHub Actions (free)** runs `.github/workflows/shorts-studio-claude.yml`:
   - **Claude** (your Pro/Max subscription via `claude setup-token`, no API key) writes a
     one-of-a-kind Remotion animation. Six sampled frames are reviewed before any upload.
   - When Claude cannot finish (usage limit, sign-in or any error), the **free backup writer**
     (`cloud/backup.py`) takes over. GitHub Models (free with Actions), or an open model
     running on the runner, writes a scene plan. A library of 12 animated scene types
     (`cloud/templates/film.tsx`) turns the plan into the film. For a typed brief, even
     with no AI at all, the brief's own sentences become the film.
   - Narration uses free edge-tts neural voices. Rendering is 1080×1920 H.264/AAC at 30 fps,
     in a process with no secrets and no network (`cloud/isolate.py`).
3. A second job saves the film to your **private library**: a draft GitHub release, visible
   only to you even in a public repository. It can also upload the film to YouTube.

## Setup (everything works from a phone browser)

1. **GitHub token for the page**: GitHub → Settings → Developer settings → Fine-grained
   tokens → Generate. Repository access: only this repository. Permissions (all **Read and
   write**): **Actions**, **Contents**, **Variables** and **Secrets**. Variables and Secrets
   let the page renew your Claude sign-in.
2. **Cloudflare API token**: Cloudflare dashboard → My Profile → API Tokens → Create Token →
   template **Edit Cloudflare Workers** → Create. Also copy your **Account ID** (Workers &
   Pages overview, right side). If you have never opened Workers & Pages, open it once so
   Cloudflare creates your free `workers.dev` subdomain.
3. In this repository: **Settings → Secrets and variables → Actions → New repository secret**:

   | Secret | Value |
   |---|---|
   | `STUDIO_GITHUB_TOKEN` | token from step 1 |
   | `CLOUDFLARE_API_TOKEN` | token from step 2 |
   | `CLOUDFLARE_ACCOUNT_ID` | account ID from step 2 |
   | `STUDIO_PASSWORD` | a long password you will type on the page |
   | `YOUTUBE_OAUTH_JSON` | optional, for YouTube upload (authorized-user JSON with a refresh token) |

4. **Actions → Shorts Studio web page → Run workflow**. The run summary shows your page
   address (`https://shorts-studio.<your-subdomain>.workers.dev`). Open it, sign in, and use
   **Add to Home screen**.
5. On the page, tap **Claude sign-in → Renew** to connect Claude. This creates the
   `CLAUDE_CODE_OAUTH_TOKEN` secret for you (see below).

Changing `STUDIO_PASSWORD` and re-running the web workflow signs every device out.

## Renewing the Claude sign-in from the page

The Claude token lasts one year. When it expires, the page shows **Claude sign-in expired**
and films keep coming from the free backup. To renew:

1. Tap **Renew**.
2. The page starts `.github/workflows/shorts-studio-claude-login.yml`, which runs the
   official `claude setup-token` in a hidden terminal on your GitHub runner
   (`cloud/claude_login.py`).
3. After about 30–60 seconds, tap **Open Claude sign-in**, sign in on claude.com,
   authorize, copy the code, paste it on the page and tap **Send code**.
4. The runner saves the new token straight into the `CLAUDE_CODE_OAUTH_TOKEN` secret. It
   is never shown, logged or sent to the page.

The link and the one-time code travel through two repository variables (`STUDIO_LOGIN_STATE`,
`STUDIO_LOGIN_CODE`). The code is useless on its own: the PKCE verifier never leaves the
CLI process. If your phone reloads the page while you are in the Claude app, the page
resumes the same sign-in. A computer also works: `claude setup-token`, then paste the token
into the secret.

## Using it

- **Create**: brief, length, narrator and writer:
  - **Claude → free backup** (default): Claude first, the backup takes over if needed.
  - **Claude only**: no backup.
  - **Free backup only**: never uses Claude.

  Optionally post to YouTube. Films take about 10–20 minutes, and you can close the page.
- **Library**: play, download, share (Android/iOS share sheet to TikTok, Instagram,
  WhatsApp…) or delete. Each film shows whether Claude or the backup wrote it, and why the
  backup was used. The newest 40 films are kept.
- **Autopilot**: subject area, audience, language, days, narrator and YouTube options. It
  runs daily at 15:23 UTC; edit the `cron` line in the workflow to change the time.
  - With Claude, autopilot researches a fresh, evidenced topic on the web.
  - With the backup, the backup writer picks a topic without live research.
  - Backup films are only posted automatically when **Also post backup films** is on.

## What is free, and the limits

- **Cloudflare Workers free plan** covers the page.
- **GitHub Actions**: unlimited minutes on public repositories. Private repositories get
  GitHub's monthly free minutes, roughly 10–20 minutes per film; the offline model adds a
  few minutes.
- **Claude** usage counts against your subscription. When it runs out, the backup keeps
  going and Claude is used again once your limit resets.
  - Use this for your own channel only. Don't run your subscription as a service for other people.
- **GitHub Models** has daily request limits. When they are reached, the offline open model
  (Ollama, `qwen2.5:7b`) is downloaded and run on the runner. Set `STUDIO_OFFLINE_MODEL=off`
  in the workflow to disable it.
- **Backup quality**: backup films use the template library, so they look polished and
  smooth but more alike than Claude's bespoke animations.
- **TikTok and Instagram**: no automatic posting; use **Share** from the Library.
- Reference-folder uploads and the Android Director console remain on the Modal service.

## Running locally

```bash
python -m unittest discover -s shorts-studio/cloud      # pipeline, backup writer, delivery
cd shorts-studio/web && node --test test/worker.test.js # web page API
```

Validation:
- All 93 cloud tests and 17 Worker tests pass, and the workflows pass actionlint.
- `wrangler deploy --dry-run` bundles the Worker.
- Every template scene type was rendered locally, in English and Arabic, and inspected.
- A complete fallback run rendered a valid 1080×1920 H.264/AAC film inside the
  network-isolated render process. In that run Claude failed and no AI writer was
  reachable, so the brief's own sentences became the film.
- The phone page was checked at Pixel 7 size in dark and light themes against a mock API.

- The real `claude setup-token` was driven headlessly up to the code exchange. It printed the
  sign-in link, accepted typed input and rejected an invalid code cleanly.

Not yet verified live, because no tokens are available in the development sandbox:
- a real Claude run, a completed sign-in renewal, GitHub Models, Ollama, and edge-tts
- the Cloudflare deploy
- GitHub's release-asset downloads

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


