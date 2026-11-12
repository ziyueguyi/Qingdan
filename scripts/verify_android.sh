#!/usr/bin/env bash
set -euo pipefail
status=0
gradle -p android connectedEmulatorDebugAndroidTest --stacktrace || status=$?
diagnostics=android/app/build/reports/androidTests/diagnostics
mkdir -p "$diagnostics"
adb pull /sdcard/Android/data/com.ziyueguyi.qingdan/files/android-ui.png "$diagnostics/android-ui.png" || true
adb logcat -d -s Qingdan:E chromium:I > "$diagnostics/runtime.log" || true
exit "$status"
