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
2. **Cloudflare**: Workers & Pages → Create → Import a repository → pick this repository.
   Keep the defaults: the build runs `npx wrangler deploy` from the repository root, where
   `wrangler.jsonc` points at `shorts-studio/web`. The Worker must be named `shortsstudio`,
   matching `wrangler.jsonc`. Every push to `main` redeploys the page.
3. In Cloudflare, open the Worker → **Settings → Variables and Secrets** → **Add**, type
   **Secret**:
   - `STUDIO_PASSWORD`: a long password you will type on the page
   - `STUDIO_GITHUB_TOKEN`: the token from step 1
4. Open the Worker's `https://shortsstudio.<your-subdomain>.workers.dev` address (Worker →
   **Visit**). Sign in and use **Add to Home screen**.
5. On the page, open **Account** (top right) and tap **Sign in with Claude**. This creates the
   `CLAUDE_CODE_OAUTH_TOKEN` secret for you (see below). The page also copies
   `STUDIO_GITHUB_TOKEN` into GitHub's Actions secrets, encrypted with the repository's
   public key, because the sign-in job needs it.

Optional:
- **YouTube**: on the page, open **Account → YouTube**. Add the address shown there to your
  Google OAuth client's **Authorized redirect URIs**, publish the consent screen (**In
  production**, or the connection stops after 7 days), then choose the client JSON file and sign
  in with Google.
  - The client file is kept in the `STUDIO_YOUTUBE_CLIENT` repository variable, encrypted with
    a key derived from your studio password.
  - The refresh token is sealed into the `YOUTUBE_OAUTH_JSON` secret.
  - Until Google audits your API project, uploads stay private: switch them to public in
    YouTube Studio.
- **Deploying from GitHub Actions instead of Cloudflare's Git integration**: add
  `CLOUDFLARE_API_TOKEN` (template **Edit Cloudflare Workers**), `CLOUDFLARE_ACCOUNT_ID`,
  `STUDIO_PASSWORD` and `STUDIO_GITHUB_TOKEN` as GitHub secrets, then run **Actions →
  Shorts Studio web page**.

Changing `STUDIO_PASSWORD` and re-running the web workflow signs every device out.

## Renewing the Claude sign-in from the page

The Claude token lasts one year. When it expires, the page shows **Claude sign-in expired**
and films keep coming from the free backup. To renew:

1. Tap **Renew** (or **Account → Sign in with Claude**).
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

- **Create · Library · Autopilot · Settings** tabs, with **Account** at the top right. Two
  colours, light and dark.
- **Create**: describe the film, pick a length, tap **Create film**. Narrator, writer, fact
  lookup and YouTube sit under **Film options**. Films in progress show their step and can be
  cancelled.
- **Settings**: choose what happens **if Claude is unavailable**:
  - **Use free backup** (default)
  - **Don't make the film**: Claude only
  - **Always use backup**

  This applies to new films and to autopilot. Settings also hold film defaults and the
  theme (System, Light, Dark).
- **Account**: Claude status (connected, expired, usage limit) with **Sign in with Claude**,
  YouTube status, and sign out.
- Sign out, cancel and delete always ask first.
- **Built for views.** Every film has:
  - burned-in, word-by-word captions with the spoken word highlighted (Settings → Captions)
  - a hook on screen and in the voice from the first frame, and a picture change every 2–3
    seconds
  - a last line that loops back to the opening
  - up to 10 hashtags (YouTube ignores all of them past 15): `#Shorts` first, then your own
    (**Settings → Hashtags on every post**, up to 8), then the writer's topic hashtags and
    automatic ones from the title. In the Publish sheet each film shows its hashtags as chips you
    can remove or add to, and **Add to all films** adds one everywhere. The first topic hashtags
    also end the title when they fit.
  - a YouTube package from the writer: a title under 60 characters with the keyword early; a
    description that opens with keywords and ends with a question; `#Shorts` first plus 2–4
    topic hashtags; search tags; category; and language
- **Narration and picture stay in sync.** Every scene has its own spoken line, recorded and
  measured separately. Each scene appears exactly while its line is spoken, and the film ends
  under a second after the last word. The length you pick sets how much narration is written;
  the film is as long as the narration. Films take about 10–20 minutes, and you can close the page.
- **The voice never stops a film.** Microsoft's free voice service sometimes drops requests. Each
  line is retried for about a minute; if the service is still down, the whole film is recorded with
  another Microsoft voice, and if that fails too, with Piper, a free open-source voice that runs on
  GitHub's machine with no internet. One film always uses one voice.
- **Never in my films.** In Settings you type what no film may mention or show (the list starts
  empty). A word that names a whole category also blocks its related words (alcohol also
  blocks beer, wine and so on, in English and Arabic). Claude and the free backup get these rules when choosing topics and writing; the
  finished film's narration, on-screen text, titles, descriptions, hashtags and image names are then
  checked in English and Arabic. A film that breaks a rule is rewritten once, and if it still does,
  it is stopped and never saved or posted. Topic ideas that touch the list are dropped. Medicines
  are allowed; only recreational and illegal drugs are blocked.
- **Every film ends with a Like & Subscribe card**: about 2 seconds after the last word, the Like
  button fills and Subscribe turns to Subscribed with a ringing bell (in Arabic for Arabic films).
  Turn it off in Settings → Like & Subscribe ending.
- **Retries cost no Claude usage.** Autopilot tries a post time twice at most. When the first try
  got as far as Claude's finished film and then failed (voice, render), the second try reuses that
  film from the first run's files and only redoes the failed steps. Claude is not asked again.
- **Publish from the Library**: tap **Select**, tick up to 20 films, and tap **Publish**.
  - Choose **Now**, **Post times** (each film takes the next free Autopilot post time) or
    **Pick time** (more films follow an hour apart), and a visibility.
  - A single film can also be published from its player.
  - `.github/workflows/shorts-studio-publish.yml` uploads them with the same never-twice
    ledger and writes the YouTube link or scheduled time back to the library.
  - Posters show **Publishing…**, **Scheduled**, **On YouTube** or **Not posted**.
- **Library**: tap a poster to play it. **Share** opens the phone's share sheet (TikTok,
  Instagram, WhatsApp…). **Save** downloads it. Delete asks to confirm. Each film shows whether Claude or the backup wrote it, and why the
  backup was used. The newest 40 films are kept.
- **Activity and notifications**: the bell at the top opens Activity.
  - A health line shows whether the hourly Autopilot check is running and when the next post
    is.
  - A timeline lists films ready, posted or scheduled, failures, sign-in problems and new ideas.
  - **Turn on notifications** to get them on your phone (Android Chrome; on iPhone, add the
    page to the Home Screen first).
  - How it works: jobs report to the page with a key the page creates itself
    (`STUDIO_NOTIFY_KEY` secret, `STUDIO_PAGE_URL` variable). Events live in the
    `STUDIO_EVENTS` variable. Phones are woken with standard Web Push (VAPID keys and
    subscriptions are kept encrypted in `STUDIO_PUSH`).
- **Up next topic ideas**:
  - Claude researches what people find interesting in your subject area and keeps a ranked
    list of about 10 ideas (`shorts-studio/autopilot/ideas.json`), each with a reason. The free
    backup writer suggests ideas when Claude is unavailable.
  - Tap an idea to make it now, make it next, change its wording or remove it, or add your own.
  - Autopilot makes the top idea at each post time and takes it off the list; with an empty
    list Claude researches a topic itself.
  - The hourly check asks for new ideas when fewer than 3 are left (at most every 6 hours).
    **Find new ideas** asks now (`shorts-studio-ideas.yml`).
  - The Create page shows the ideas as chips: tap one to fill the brief.
- **Topic choices**:
  - Subject area: tap one or more subjects (Science and general knowledge, Space, the human
    body, animals…) plus an optional custom one.
  - Audience, language, tone and timezone are lists, and post times are added from a
    half-hour list.
- **Autopilot post times**: add up to 6 times per day to the exact minute (hour and minute
  pickers), for example 06:47 and 17:30, with days and timezone. The check runs every 5
  minutes and starts each film shortly before its time; public films go live on YouTube at
  exactly that minute.
  - The page keeps time itself: a Cloudflare Cron Trigger (`wrangler.jsonc`, every 5 minutes)
    starts each film once, up to 50 minutes before its post time. GitHub's own schedules were
    seen running hours late, so `shorts-studio-autopilot.yml` is now a manual fallback only.
  - Saving the settings also checks at once, so a time that is close or just passed starts
    right away.
  - With **Post to YouTube** on and visibility **Public**, the film is uploaded private with
    YouTube's `publishAt`, so YouTube publishes it at exactly that time.
  - A late film (up to 2.5 hours after its time) is posted at once.
- **Autopilot**: one switch, a subject line and days. Changes save automatically, and more
  settings sit under "More autopilot settings". It
  runs daily at 15:23 UTC; edit the `cron` line in the workflow to change the time.
  - With Claude, autopilot researches a fresh, evidenced topic on the web.
  - With the backup, the backup writer picks a topic without live research.
  - Backup films are only posted automatically when **Also post backup films** is on.

## Deleted posts

Every hour, and when you tap **Refresh** in the Library, the page checks whether published posts
still exist. A Short you delete on YouTube or a Reel you delete on Instagram stops showing as
published, Activity notes it, and the film can be published again (the YouTube upload record is
cleared). A Short you only make private still counts as uploaded. Instagram posts always go up as
Reels, and YouTube uploads are vertical and short, so YouTube shows them as Shorts.

## Autopilot topics

Autopilot runs a list of **topics**. Each topic has its own subject, audience, language, tone,
narrator, length, days and post times, its own **Up next** ideas, and its own accounts (**Post to**:
YouTube and any connected Instagram accounts). Timezone, writer, captions, hashtags, the never-list
and "Also post backup films" are shared.

At each post time the page's clock starts that topic's film; the run is named
`Autopilot film <time>#<topic>`, so the film uses that topic's settings and Up next list and posts
only to that topic's accounts. A post time is still made once, with at most one retry. Settings saved
before topics existed become the first topic. Finding new ideas works per topic; the automatic
top-up asks for ideas for every topic with fewer than three.

## Instagram Reels

Films can go to one or more Instagram accounts as Reels: from **Publish** in the Library, from
**Film options** when you create a film, and from **Autopilot → Publishing** for every autopilot
film. It uses Meta's official Instagram API with Instagram Login (free, no Facebook Page needed).

Set up once (Account → Instagram → Connect shows the same steps):

1. Make each Instagram account **Professional** (Creator is fine): Instagram → Settings →
   Account type and tools. Personal accounts cannot be posted to.
2. At https://developers.facebook.com/apps/ create an app with the use case **Manage messaging &
   content on Instagram**. New apps use Meta's **Unified login** (it asks for a business portfolio).
3. App roles → Roles → add each account as an **Instagram tester**, then accept the invite at
   https://www.instagram.com/accounts/manage_access/ (Apps and websites).
4. Use cases → Customize → API setup → **Generate access tokens**: add the account, tap
   **Generate token** and copy it.
5. On the page: Account → Instagram → **Connect**, paste the token (and the account ID if the page
   asks for it). Use **Add account** for more accounts.

Both token kinds work: Instagram-login tokens (start with IG, 60 days, renewed automatically) and
Unified-login tokens (start with EAA, do not expire). Apps that already saved an app ID and secret
can also sign in through Instagram.

How it works: the account tokens (60 days) are kept encrypted in the `STUDIO_INSTAGRAM` repository
variable and renewed automatically. Posts wait in `STUDIO_IG_QUEUE`; the page's one-minute clock
asks Instagram to fetch the film through a signed link that works for two hours, waits until
Instagram has processed it, and publishes it at the post time. Instagram allows 100 posts per account
per day through the API. Reels are always public. Activity shows when a Reel is posted or fails.

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


