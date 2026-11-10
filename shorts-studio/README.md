# Shorts Studio 0.5.0

Describe a film and tap **Create with Claude**. Opus 5.5 writes a complete, original React/Remotion animation for the brief: custom SVG illustrations, typography, masks, camera moves, easing and transitions. The service records neural narration and renders a 1080×1920 H.264/AAC MP4 at 30 fps. The Android app downloads that finished film to its Library. The main creation flow no longer uses the phone's TTS engine or the restricted 432×768 vector timeline.

## Narration

Choose automatic language selection or a neural narrator in English or Arabic. Available choices include Guy, Jenny, Ryan, Sonia, Rami, Layla, Hamed and Zariyah. The `edge-tts` project provides access to Microsoft's online neural voices without a speech API key. It requires internet access and availability of that service. Speech errors are reported; there is no silent fallback to Android TTS. Narration keeps its natural pace; the animation can extend slightly to accommodate the recording, up to 75 seconds.

## Rendering

Claude supplies actual TSX animation code, not a list of headings or a fixed decoration template. A separate, bounded Modal Sandbox compiles and renders that code. It has no network access, secrets or access to the Claude credential Volume. The render image includes pinned Remotion 4.0.421 packages, Chromium, FFmpeg and local English/Arabic fonts. Narration is added outside the generated component. Failed renders are sent back to Claude for one code repair; recorded narration is preserved.

Creation uses a job ID with authenticated status polling and download endpoints. The app can resume polling the same job after a connection interruption or reopening the same request, without deliberately starting another generation. Downloads use a temporary file and only become visible after completion. Five recent cloud projects are retained; downloaded phone videos remain in the Library until deleted. Interrupted cloud jobs are marked failed on service restart.

## Install/update

Download the APK artifact from the latest successful **Build Shorts Studio APK** run on `main`. Version code 6, package `com.shortsstudio.app`. The workflow retains the same branch's debug signing key so updates can preserve local videos and the existing server connection. Do not uninstall an earlier version unless a signing mismatch requires it; export wanted videos first if uninstalling.

Your existing private connection and Claude sign-in can remain in place when the cloud service is redeployed with the same access key. The app must be updated to use the new production endpoints. Old APKs continue to use the older `/direct` timeline endpoint.

## Cloud setup

From a checkout of this repository, install Modal and run:

```bash
cd shorts-studio/cloud
modal run modal_bridge.py
```

This builds the isolated rendering image and deploys `shorts-studio-cloud`. Open the complete private setup link it prints, including `#key=…`, tap **Connect APK**, then complete official Claude login if needed. Keep the private setup link private. Passwords stay on Claude's official authorization site. Official CLI credentials are stored in the private `shorts-studio-claude-home` Volume.

For an existing deployment, the maintainer can supply its existing 48-character hexadecimal connection key in `STUDIO_DEPLOY_KEY` to preserve pairing. Never commit that key or a Modal token. Without that variable, setup generates a new key and phones need to pair again. The `cloud/production.py` module and `cloud/render/` directory must accompany `modal_bridge.py`; copying only the bridge into Playground is no longer sufficient for this version.

Modal compute consumes the account's hosting credits. Claude usage counts against the signed-in subscription. No paid speech API is configured. Do not assume unlimited free cloud rendering. Only one film is accepted at a time. Keep the app open while creating/downloading; Android can delay background jobs. Existing daily director schedules use the new cloud film pipeline after this APK update. Older legacy schedules retain their previous local behavior.

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

The APK workflow runs cloud and Android checks. Cloud tests cover code validation, neural voice selection, render repair, natural speech timing, interrupted jobs and sandbox isolation. HTTP tests cover authenticated polling, bounded result redirects and rejecting incomplete/non-video downloads. Live verification should additionally create a narrated MP4 and inspect its audio/video tracks and rendered frames.

The older `server/`, `motion/`, Android `MobileVideo` and `/direct` implementation remain for compatibility with old installations; the new creation flow uses `cloud/production.py` and `cloud/render/render.mjs`.
