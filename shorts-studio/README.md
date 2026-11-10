# Shorts Studio 0.4.1 — Claude directs the motion (phone-first)

Describe the motion graphic and tap **Create with Claude**. The official Claude Code CLI uses `claude-opus-5-5` at high effort to design narration, scene timing, gradients, typography, vector geometry and each layer's position, scale, rotation and opacity keyframes. There is no motion-style picker. Android executes the custom timeline at 24 fps and adds its installed TTS narration to the portrait MP4. No Anthropic API key is requested.

The animation language supports text, rectangles, ellipses, lines and polygons, with independent easing and keyframes. It produces 2D vector motion graphics; it does not execute arbitrary animation code or generate photographic footage, music or 3D models. Output is 432×768. Opus access depends on the signed-in account; failures are reported without substituting another model. Timelines are schema-validated, with one automatic repair attempt before returning a clear error.

## Install

Download `ShortsStudio-debug-apk` from the latest successful **Build Shorts Studio APK** run on `feature/shorts-studio-modal`, unzip it, and install `app-debug.apk`. Version code 5; package `com.shortsstudio.app`. Version 0.4.1 starts retaining the debug signing key as a private GitHub Actions artifact for subsequent same-branch builds (90-day retention refreshed by successful push builds). Earlier APKs used per-runner keys, so export videos you want to keep before uninstalling v0.4 or older to install this build. Uninstalling removes app-local videos and drafts; the cloud Claude login is separate.

## HTTP result redirects

Modal sends a 303 result redirect for requests exceeding 150 seconds. The APK follows that URL as an authenticated GET only when scheme, host, port and path match the original paired request. It never resubmits the brief, forwards credentials to a different origin, or follows an unbounded redirect chain. The total request deadline and response-size limit remain enforced. This fix does not require a Modal redeploy or a new Claude sign-in.

## One-time cloud setup from your phone

1. Open [`cloud/modal_bridge.py`](cloud/modal_bridge.py) in GitHub, use its raw view and copy the entire file.
2. Open [Modal Playground](https://modal.com/playground/get_started), replace the example code with that file, then tap **Run**. Stay signed into your own Modal account.
3. The script deploys `shorts-studio-cloud` and prints **OPEN THIS PRIVATE SETUP LINK ON YOUR PHONE**. Open that complete link, including its fragment. Do not share it: it authorizes access to your private script service.
4. Tap **Connect APK** and confirm the displayed Modal server in the Android dialog. The app saves the connection encrypted with Android Keystore; there are no runner URL/token fields to fill in.
5. Open the private setup page in Chrome and tap **Start official Claude login**. Open its official Claude authorization link and approve your Pro account. Copy the complete code using Claude's Copy button, return to the setup page, paste it and tap **Send code to Claude**. The page displays **Code sent to Claude** only after the server acknowledges submission; an expired session gives an explicit error and preserves your input. Password entry stays on Claude's official site. Version 0.4 accepts both official `claude.ai` and `claude.com` authorization links; Chrome can also complete login for the paired APK.
6. Wait for **Claude signed in**. Return to Create, describe your motion graphic and tap **Create with Claude**. Narration and motion design are generated and rendered in one operation; the default narrator follows the narration language. A duration target and voice override remain optional.
7. Close and reopen the connection screen to confirm the login survives. For a full persistence check, allow the Modal container to stop, then reopen and check again.

Pairing the APK does not mean Claude is authenticated. Re-running the setup deploys a new app access key, so pair again from the new printed link. Existing official Claude credentials stay in the `shorts-studio-claude-home` Modal Volume. Disconnecting the phone removes only its local connection; it does not revoke the cloud CLI login. Stop the Modal app and remove its Volume to remove that cloud state when no longer needed.

## Cost and runtime limits

Modal hosting is separate from Claude Pro. The screenshot-tested account has $1 of credit available before adding a payment method. This implementation does not add payment methods or change spending settings. Trial accounts may restrict persistent deployment even when a small Playground function works. A real deployment is required before the connection works.

The service requests 4 GB memory, scales to zero, and allows only one container. Login sessions run on the server for at most five minutes independently of page connections; reopening the page resumes the current session. Code submission uses authenticated HTTP and a session ID to reject codes from an earlier attempt. The container's idle window is six minutes to cover browser switching; idle resources consume hosting credit. Opus direction requests allow up to four minutes per design attempt and one repair attempt. Legacy script requests have a three-minute timeout. Pairing and official CLI launch have been observed on the deployed account, but the revised login flow and completed subscription authorization still require live verification after updating the deployment.

## What still runs on Android

- Video encoding uses MediaCodec/MediaMuxer, motion typography and installed Android TTS voices. No fixed pack of 54 voices is included.
- The main creation flow requires the paired Claude server. It reports design failures instead of silently replacing them with a generic template.
- The Library previews local videos and opens Android's share sheet for YouTube. This does not authorize unattended YouTube uploads.
- New daily WorkManager jobs request a fresh Opus timeline and render it on Android at approximate times, with internet required. Power saving can delay or stop them. Old scheduled jobs retain their legacy behavior. Always-on cloud rendering and unattended publishing are not implemented by this update.

## Verification

Python tests: `python -m unittest discover -s shorts-studio/cloud -v` (install Modal, FastAPI and HTTPX first).
Android: from `shorts-studio`, run `gradle :app:testDebugUnitTest :app:assembleDebug` with Android SDK 35 and Gradle 8.10.2. The existing GitHub Actions workflow runs both checks.

The historical `server/` runner is not used by v0.3. The active cloud service, including the director schema, is the single file `cloud/modal_bridge.py`.

## Recovered phone-first build

This is the original Modal-backed mobile version restored from `HadiMortada/DoTest` branch `feature/shorts-studio-modal`. Its APK opens the official Claude approval-code setup page and renders MP4 files locally using Android MediaCodec and TTS. It does **not** use runner URL/token fields. If you have an existing private Modal setup link, connect this APK using its **Connect APK** button. Otherwise deploy `shorts-studio/cloud/modal_bridge.py` from your phone via Modal Playground, then pair and approve Claude. Do not share the setup link or authorization code. The bridge retains user credentials inside your private Modal Volume. Hosting is independent from Claude Pro and may consume paid Modal credits. The legacy server/ and motion/ directories remain in the repository for reference but are not part of the restored phone APK.


## One-app daily experience: persistent cloud + automatic reconnection

The phone app already contains the official Claude approval-code login panel,
and renders video locally. **Modal is a backend, not a second everyday app**.

To avoid opening Modal Playground and pressing Run each time, the initial
`shorts-studio-cloud` service must exist as a **persistent Modal deployment**.
The current `modal_bridge.py` local entrypoint calls `cloud.deploy(...)`;
ensure that this call actually succeeds and the Modal Dashboard shows the
app in the **deployed** state. A transient Playground run that does not
produce a deployed app is not sufficient. Trial limits or account restrictions
may prevent deployment.

Once deployed, the cloud can scale to zero when idle and automatically wake
on a request. Keep the saved service and private pairing key instead of
running the deploy script again (that script rotates the access key).

In the updated Android Accounts tab:
1. If not paired, **paste the private Modal setup link** once, then tap
   **Connect my private cloud**. The app stores the key encrypted in Android
   Keystore. Do not share or commit the full link (including its fragment).
2. Tap **Sign in / Manage Claude** to open the existing official Claude Code
   authorization page inside ShortsStudio (with official Claude approval in
   Chrome). Only sign in again when the cloud login actually expires.
3. Tap **Check / Reconnect** to wake a deployed cloud and inspect whether
   Claude is already signed in. You can then create your Short normally.

The app does **not** hold a Modal account token or deploy cloud infrastructure
from the phone by itself. Automating that requires a separate, explicitly
authorized Modal account integration and would not eliminate cloud compute
costs. Claude subscription access is governed by Anthropic's current
third-party integration and billing terms; a third-party bridge is not
equivalent to an official Claude mobile client.
