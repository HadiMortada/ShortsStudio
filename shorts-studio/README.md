# Shorts Studio 0.5.0

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

The APK workflow runs cloud and Android checks. Cloud tests cover code validation, neural voice selection, render repair, natural speech timing, interrupted jobs, job spawning independent of web shutdown and sandbox isolation. HTTP tests cover authenticated polling, bounded result redirects and rejecting incomplete/non-video downloads. Live verification should additionally create a narrated MP4 and inspect its audio/video tracks and rendered frames.

The older `server/`, `motion/`, Android `MobileVideo` and `/direct` implementation remain for compatibility with old installations; the new creation flow uses `cloud/production.py` and `cloud/render/render.mjs`.
