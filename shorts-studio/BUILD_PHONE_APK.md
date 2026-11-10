# Build and verify the phone-first Android APK

This directory is the **phone-first** ShortsStudio restored from `HadiMortada/DoTest` branch `feature/shorts-studio-modal`. The source is in `shorts-studio/`, not the PHP project in DoTest.

The app must retain:

- `CloudPairActivity` and `CloudLoginActivity` for the official Claude approval-code flow through the owner's private Modal service.
- `MobileVideo` with Android `MediaCodec` / `MediaMuxer` MP4 encoding.
- `MobileJobs` on-device scheduled renders and installed Android TTS.
- Independent Claude creative design and normal tool permissions.
- No required runner URL or runner access token.

For Android build, use JDK 17, Android SDK 35 and Gradle 8.10.2, then:

```bash
cd shorts-studio
gradle :app:testDebugUnitTest :app:assembleDebug --stacktrace --no-daemon
```

A successful build produces `shorts-studio/app/build/outputs/apk/debug/app-debug.apk`. Confirm non-zero size, ZIP/APK format, and package `com.shortsstudio.app`. Do not claim a downloadable APK is available until its bytes exist and the artifact is accessible.

GitHub Actions previously failed before any runner was assigned (no steps, zero runner minutes). If this persists, build in an authorized Codex cloud development environment or another Android build environment instead. Do not convert the private repository to public or add credentials or paid integrations to bypass build availability.

Never substitute the old runner-based APK: it is incompatible with the phone approval-code and on-device MP4 workflow.
