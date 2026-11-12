#!/usr/bin/env bash
set -euo pipefail
status=0
gradle -p android connectedEmulatorDebugAndroidTest --stacktrace || status=$?
diagnostics=android/app/build/reports/androidTests/diagnostics
mkdir -p "$diagnostics"
if ! adb pull /sdcard/Android/data/com.ziyueguyi.qingdan/files/android-ui.png "$diagnostics/android-ui.png"; then
  # AGP normally uninstalls test applications, including their external files.
  # Reopen the built debug APK for a genuine native screenshot after cleanup.
  apk=$(find android/app/build/outputs/apk/emulator/debug -name '*.apk' -print -quit)
  adb install -r -g "$apk"
  adb shell am start -W -n com.ziyueguyi.qingdan/.MainActivity
  sleep 10
  adb exec-out screencap -p > "$diagnostics/android-ui.png"
  printf '%s\n' 'reopened-after-instrumentation-cleanup' > "$diagnostics/screenshot-source.txt"
fi
python -c 'from pathlib import Path; p=Path("android/app/build/reports/androidTests/diagnostics/android-ui.png"); assert p.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), "Native screenshot missing"'
adb logcat -d -s Qingdan:E chromium:I > "$diagnostics/runtime.log" || true
exit "$status"
