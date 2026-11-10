# Daily approved storyboards

GitHub Actions renders at 18:00 UTC each day (adjust the cron in
.github/workflows/shorts-studio-daily.yml). The scheduled job only renders a
reviewed storyboard named YYYY-MM-DD.json in this folder. If today's file is
missing it skips, avoiding random misinformation and repeated content.

Use the format in shorts-studio/motion/example-plan.json. Draft with Claude
Code, verify claims and copyrights, then commit the approved file. No
credentials or generated videos belong in git.

For a one-off preview: GitHub Actions > Shorts Studio approved daily video >
Run workflow, select shorts-studio/motion/example-plan.json, and leave publish
unchecked. The resulting MP4 is available as a workflow artifact.

For OPTIONAL scheduled upload, set the YOUTUBE_OAUTH_JSON secret to an
authorized_user JSON OAuth refresh-token payload; set repository variable
SHORTS_AUTOPUBLISH=true. Default visibility is private; optional
SHORTS_PRIVACY may be private, unlisted, or public.

GitHub creates a publish-attempt receipt in shorts-studio/ledger/ BEFORE
upload. If the network fails, do not automatically retry without inspecting
YouTube Studio to reconcile a potentially successful upload.

Important: Claude Pro subscription usage does NOT transfer to GitHub-hosted
runners or provide API credits. The existing Android-controlled, locally
authenticated Claude Code runner is still needed for genuinely new Claude
scripts every day. Cloud rendering only uses plans you previously approved.
GitHub's private-repo compute allowance and extra billing are separate.
