#!/usr/bin/env bash
# One-shot Android 17 emulator acceptance: v0.3.1 -> current emulatorDebug -> reboot.
# This script never clears or uninstalls the application after the baseline is installed.
set -euo pipefail

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
PACKAGE=com.ziyueguyi.qingdan
RUNNER=androidx.test.runner.AndroidJUnitRunner
TEST_CLASS=com.ziyueguyi.qingdan.AndroidDeviceRecoveryTest
BASELINE_TAG=v0.3.1
BASELINE_COMMIT=14d640348711fb1898aa08f4f10a1f80de652f81
DIAGNOSTICS=android/app/build/reports/androidTests/diagnostics
RECOVERY_REPORT="$DIAGNOSTICS/recovery"
DEVICE_REPORT=/sdcard/Android/data/$PACKAGE/files/recovery
CURRENT_VERSION=$(python -c 'import tomllib; print(tomllib.load(open("pyproject.toml", "rb"))["project"]["version"])')
mkdir -p "$RECOVERY_REPORT"
rm -f "$RECOVERY_REPORT"/*
adb get-state >/dev/null
[[ $(adb shell getprop ro.kernel.qemu | tr -d '\r') == 1 ]] || {
  echo 'Recovery acceptance requires an Android emulator (ro.kernel.qemu=1).' >&2
  exit 1
}
[[ $(adb shell getprop ro.build.version.sdk | tr -d '\r') == 37 ]] || {
  echo 'Recovery acceptance requires Android 17 / API 37.' >&2
  exit 1
}
[[ $(adb shell getprop ro.build.version.release | tr -d '\r') == 17* ]] || {
  echo 'Recovery acceptance requires an Android 17 system image.' >&2
  exit 1
}
[[ $(adb shell getprop ro.product.cpu.abi | tr -d '\r') == x86_64 ]] || {
  echo 'The emulatorDebug APK requires an x86_64 emulator.' >&2
  exit 1
}
adb shell rm -rf "$DEVICE_REPORT"

WORKDIR=$(mktemp -d)
cleanup() {
  status=$?
  set +e
  # Emergency restoration also runs after an interrupted/failed instrumentation method.
  adb shell cmd power set-mode 0 >/dev/null 2>&1
  adb shell input keyevent KEYCODE_WAKEUP >/dev/null 2>&1
  adb shell wm dismiss-keyguard >/dev/null 2>&1
  adb shell dumpsys battery reset >/dev/null 2>&1
  adb pull "$DEVICE_REPORT/." "$RECOVERY_REPORT/" >/dev/null 2>&1
  rm -rf "$WORKDIR"
  exit "$status"
}
trap cleanup EXIT

{
  echo "baseline_tag=$BASELINE_TAG"
  echo "baseline_commit=$BASELINE_COMMIT"
  echo "current_version=$CURRENT_VERSION"
  echo "device_fingerprint=$(adb shell getprop ro.build.fingerprint | tr -d '\r')"
  echo "device_abi=$(adb shell getprop ro.product.cpu.abi | tr -d '\r')"
  echo 'scope=emulatorDebug/x86_64 CI debug signing; no physical-device or arm64 upgrade claim'
} > "$RECOVERY_REPORT/environment.txt"

# Fetch the actual remote tag, then archive its source separately from the working tree.
GIT_TERMINAL_PROMPT=0 git fetch --no-tags --depth=1 origin refs/tags/$BASELINE_TAG
FETCHED_COMMIT=$(git rev-parse 'FETCH_HEAD^{commit}')
[[ $FETCHED_COMMIT == "$BASELINE_COMMIT" ]] || {
  echo "Published $BASELINE_TAG resolved to unexpected commit $FETCHED_COMMIT" >&2
  exit 1
}
mkdir "$WORKDIR/baseline"
git archive "$BASELINE_COMMIT" | tar -x -C "$WORKDIR/baseline"
[[ $(python -c 'import tomllib,sys; print(tomllib.load(open(sys.argv[1], "rb"))["project"]["version"])' "$WORKDIR/baseline/pyproject.toml") == 0.3.1 ]]

# Each source tree must run its own preparer; their generated/Gradle paths must not mix.
python scripts/prepare_android.py
(
  cd "$WORKDIR/baseline"
  python scripts/prepare_android.py
)
gradle -p "$WORKDIR/baseline/android" assembleEmulatorDebug --stacktrace \
  > "$RECOVERY_REPORT/baseline-gradle.log" 2>&1
gradle -p android assembleEmulatorDebug assembleEmulatorDebugAndroidTest --stacktrace \
  > "$RECOVERY_REPORT/current-gradle.log" 2>&1

BASELINE_APK="$WORKDIR/baseline/android/app/build/outputs/apk/emulator/debug/app-emulator-debug.apk"
CURRENT_APK="$ROOT/android/app/build/outputs/apk/emulator/debug/app-emulator-debug.apk"
TEST_APK="$ROOT/android/app/build/outputs/apk/androidTest/emulator/debug/app-emulator-debug-androidTest.apk"
for apk in "$BASELINE_APK" "$CURRENT_APK" "$TEST_APK"; do
  [[ -s $apk ]] || { echo "Expected Gradle APK is missing: $apk" >&2; exit 1; }
done

APKSIGNER=${APKSIGNER:-${ANDROID_HOME:-}/build-tools/37.0.0/apksigner}
if [[ ! -x $APKSIGNER ]]; then APKSIGNER=$(command -v apksigner || true); fi
[[ -n $APKSIGNER && -x $APKSIGNER ]] || { echo 'apksigner is required' >&2; exit 1; }
: > "$RECOVERY_REPORT/signing.txt"
for apk in "$BASELINE_APK" "$CURRENT_APK" "$TEST_APK"; do
  "$APKSIGNER" verify --verbose --print-certs "$apk" >> "$RECOVERY_REPORT/signing.txt"
done
certificate_digest() {
  "$APKSIGNER" verify --print-certs "$1" | awk -F': ' '/certificate SHA-256 digest:/{print $NF; exit}'
}
BASELINE_CERT=$(certificate_digest "$BASELINE_APK")
CURRENT_CERT=$(certificate_digest "$CURRENT_APK")
TEST_CERT=$(certificate_digest "$TEST_APK")
[[ -n $BASELINE_CERT && $BASELINE_CERT == "$CURRENT_CERT" && $BASELINE_CERT == "$TEST_CERT" ]] || {
  echo 'Baseline, current app, and test APK must use the same CI debug signing identity.' >&2
  exit 1
}
{
  echo "baseline_sha256=$BASELINE_CERT"
  echo "current_sha256=$CURRENT_CERT"
  echo "android_test_sha256=$TEST_CERT"
  echo 'signing_scope=same ephemeral CI debug identity only'
} > "$RECOVERY_REPORT/signing-summary.txt"
sha256sum "$BASELINE_APK" "$CURRENT_APK" "$TEST_APK" > "$RECOVERY_REPORT/apk-sha256.txt"

# A previous local/current install may be newer and cannot be downgraded with -r.
# Remove it only during initial baseline setup. No uninstall or pm clear occurs below.
adb uninstall "$PACKAGE" > "$RECOVERY_REPORT/initial-uninstall.txt" 2>&1 || true
adb uninstall "$PACKAGE.test" >> "$RECOVERY_REPORT/initial-uninstall.txt" 2>&1 || true
adb install -r -g "$BASELINE_APK" | tee "$RECOVERY_REPORT/install-baseline.txt"
grep -q Success "$RECOVERY_REPORT/install-baseline.txt"
adb install -r -g "$TEST_APK" | tee "$RECOVERY_REPORT/install-test.txt"
grep -q Success "$RECOVERY_REPORT/install-test.txt"
adb shell dumpsys package "$PACKAGE" > "$RECOVERY_REPORT/package-baseline.txt"

run_stage() {
  local method=$1 stage=$2 log="$RECOVERY_REPORT/$2" result
  shift 2
  set +e
  adb logcat -c >/dev/null 2>&1
  adb shell am instrument -w -r -e recovery_stage true \
    -e expected_version "$CURRENT_VERSION" -e class "$TEST_CLASS#$method" \
    "$PACKAGE.test/$RUNNER" "$@" > "$log" 2>&1
  result=$?
  set -e
  cat "$log"
  # WorkManager's own log lines explain scheduling decisions; keep them per stage.
  adb logcat -d -v threadtime 2>/dev/null | grep -E 'WM-[A-Za-z]+:|WM-' > "$RECOVERY_REPORT/workmanager-$stage.log" || true
  adb pull "$DEVICE_REPORT/." "$RECOVERY_REPORT/" >/dev/null 2>&1 || true
  [[ $result -eq 0 ]] || { echo "Instrumentation $method exited $result" >&2; return "$result"; }
  grep -Eq 'OK \(1 test\)' "$log" || {
    echo "Instrumentation $method did not report exactly one passing test." >&2
    return 1
  }
}

run_stage prepareBaseline baseline-instrumentation.txt
# am instrument -w has returned; stop the v0.3.1 process before replacing its APK.
# Later stages are not force-stopped: the reboot must exercise normal WorkManager restoration.
adb shell am force-stop "$PACKAGE"
# Overwrite only: no -d, no uninstall, no pm clear, and no deletion of files/data.
adb install -r -g "$CURRENT_APK" | tee "$RECOVERY_REPORT/install-current-overwrite.txt"
grep -q Success "$RECOVERY_REPORT/install-current-overwrite.txt"
adb shell dumpsys package "$PACKAGE" > "$RECOVERY_REPORT/package-current.txt"
run_stage verifyUpgrade upgrade-instrumentation.txt
run_stage alertWorkerUnderLockAndPowerSaver alert-instrumentation.txt
adb shell dumpsys power > "$RECOVERY_REPORT/power-before-reboot.txt"
adb shell dumpsys jobscheduler > "$RECOVERY_REPORT/jobscheduler-before-reboot.txt"
# WorkManager 2.10 enables SystemAlarmService only below API 23; the alarm dump is kept as
# supporting evidence, while the gate below is the persisted JobScheduler job of each worker.
adb shell dumpsys alarm > "$RECOVERY_REPORT/alarm-before-reboot.txt"

boot_identity() {
  local value
  value=$(adb shell cat /proc/sys/kernel/random/boot_id 2>/dev/null | tr -d '\r')
  [[ -n $value ]] && { echo "/proc/sys/kernel/random/boot_id=$value"; return 0; }
  value=$(adb shell getprop ro.boot.boot_id 2>/dev/null | tr -d '\r')
  [[ -n $value ]] && { echo "ro.boot.boot_id=$value"; return 0; }
  value=$(adb shell settings get global boot_count 2>/dev/null | tr -d '\r')
  [[ -n $value && $value != null ]] && { echo "boot_count=$value"; return 0; }
  return 1
}
BOOT_BEFORE=$(boot_identity) || { echo 'No reboot identity source is readable on this device.' >&2; exit 1; }
adb reboot
adb wait-for-device
BOOT_AFTER=''
for _ in $(seq 1 90); do
  BOOT_AFTER=$(boot_identity 2>/dev/null || true)
  COMPLETED=$(adb shell getprop sys.boot_completed 2>/dev/null | tr -d '\r' || true)
  [[ -n $BOOT_AFTER && $BOOT_AFTER != "$BOOT_BEFORE" && $COMPLETED == 1 ]] && break
  sleep 2
done
[[ -n $BOOT_AFTER && $BOOT_AFTER != "$BOOT_BEFORE" && ${COMPLETED:-} == 1 ]] || {
  echo 'Emulator did not complete a new boot within 180 seconds.' >&2
  exit 1
}
printf 'before=%s\nafter=%s\n' "$BOOT_BEFORE" "$BOOT_AFTER" > "$RECOVERY_REPORT/reboot.txt"
# Restore a usable interactive screen after reboot without touching application data.
adb shell cmd power set-mode 0
adb shell input keyevent KEYCODE_WAKEUP
adb shell wm dismiss-keyguard
# Inspect the OS scheduler before instrumentation can initialize the app. A
# persisted WorkManager database row alone is not proof of boot rescheduling.
for _ in $(seq 1 30); do
  adb shell dumpsys jobscheduler > "$RECOVERY_REPORT/jobscheduler-boot-before-instrumentation.txt"
  adb shell dumpsys alarm > "$RECOVERY_REPORT/alarm-boot-before-instrumentation.txt"
  if python scripts/verify_android_jobs.py "$RECOVERY_REPORT/upgrade.json" \
      "$RECOVERY_REPORT/jobscheduler-boot-before-instrumentation.txt" \
      "$RECOVERY_REPORT/jobscheduler-verified.json" > "$RECOVERY_REPORT/jobscheduler-probe.log" 2>&1; then
    break
  fi
  sleep 2
done
python scripts/verify_android_jobs.py "$RECOVERY_REPORT/upgrade.json" \
  "$RECOVERY_REPORT/jobscheduler-boot-before-instrumentation.txt" \
  "$RECOVERY_REPORT/jobscheduler-verified.json"
run_stage verifyReboot reboot-instrumentation.txt
adb shell dumpsys jobscheduler > "$RECOVERY_REPORT/jobscheduler-after-reboot.txt"
adb shell dumpsys alarm > "$RECOVERY_REPORT/alarm-after-reboot.txt"
adb shell dumpsys deviceidle > "$RECOVERY_REPORT/deviceidle-after-reboot.txt"
adb exec-out screencap -p > "$RECOVERY_REPORT/reboot-screen.png"
adb pull "$DEVICE_REPORT/." "$RECOVERY_REPORT/" >/dev/null
adb logcat -d -s Qingdan:E AndroidRuntime:E chromium:I > "$RECOVERY_REPORT/runtime.log" || true

# Missing/stale evidence, skipped stages, invalid JSON, or a non-PNG must fail the job.
python - "$RECOVERY_REPORT" <<'PY'
import json
from pathlib import Path
import sys

root = Path(sys.argv[1])
for name in ("baseline", "upgrade", "alert", "reboot"):
    path = root / f"{name}.json"
    assert path.is_file() and path.stat().st_size > 0, f"missing evidence: {path}"
    data = json.loads(path.read_text(encoding="utf-8"))
    assert data.get("status") == "passed", f"stage did not pass: {path}: {data}"
baseline = json.loads((root / "baseline.json").read_text(encoding="utf-8"))
upgrade = json.loads((root / "upgrade.json").read_text(encoding="utf-8"))
alert = json.loads((root / "alert.json").read_text(encoding="utf-8"))
reboot = json.loads((root / "reboot.json").read_text(encoding="utf-8"))
assert baseline["version_name"] == "0.3.1"
assert upgrade["version_name"] != "0.3.1" and reboot["version_name"] == upgrade["version_name"]
assert upgrade["sqlite_verified"] is True and reboot["sqlite_verified"] is True
assert upgrade["task_id"] == baseline["task_id"] == reboot["task_id"]
assert alert["locked_power_save"] is True and alert["locked_keyguard"] is True
assert alert["locked_interactive"] is False
assert alert["alert_worker_succeeded"] is True and alert["notification_active"] is True
assert alert["restored_power_save"] == alert["original_power_save"]
assert alert["restored_interactive"] == alert["original_interactive"]
assert alert["restored_keyguard"] is False
assert baseline["boot_id"] != reboot["boot_id"]
scheduled = json.loads((root / "jobscheduler-verified.json").read_text(encoding="utf-8"))
assert scheduled["status"] == "passed"
assert set(scheduled["jobs"]) == {"qingdan-alerts", "qingdan-sync"}
assert all(scheduled["jobs"][name] for name in scheduled["jobs"])
for work_name in ("qingdan-alerts", "qingdan-sync"):
    entries = baseline["work"][work_name]
    assert entries and all(item["state"] == "ENQUEUED" for item in entries)
    # v0.3.1 (WorkManager 2.10.1) may leave one periodic worker ENQUEUED without an OS job;
    # the current version must re-register both on first open and keep them across reboot.
    baseline_unscheduled = baseline["work"]["unscheduled"]
    assert upgrade["baseline_unscheduled"] == baseline_unscheduled
    assert upgrade["healed_after_open"] == (len(baseline_unscheduled) > 0)
    for document in (upgrade, reboot):
        entries = document["work"][work_name]
        assert entries and all(item["state"] == "ENQUEUED" for item in entries)
        assert document["work"]["unscheduled"] == []
        # WorkManager on API 23+ schedules every worker through SystemJobScheduler.
        assert all(item["os_job"] is True and item["scheduler"] == "jobscheduler" for item in entries)
for name in ("locked-alert.png", "restored-screen.png", "reboot-screen.png"):
    path = root / name
    data = path.read_bytes() if path.is_file() else b""
    assert data.startswith(b"\x89PNG\r\n\x1a\n") and b"IEND" in data[-16:], f"invalid/missing PNG: {path}"
summary = {
    "status": "passed",
    "scope": "Android 17 x86_64 emulatorDebug with one CI debug signing identity",
    "baseline_tag": "v0.3.1",
    "baseline_commit": "14d640348711fb1898aa08f4f10a1f80de652f81",
    "current_version": upgrade["version_name"],
    "task_id": baseline["task_id"],
    "sqlite_survived_overwrite": True,
    "sqlite_survived_reboot": True,
    "locked_power_saver_alert": True,
    "periodic_work_after_reboot": ["qingdan-alerts", "qingdan-sync"],
    "periodic_systemjobservice_jobs_present_before_post_reboot_instrumentation": True,
    "baseline_workers_unscheduled_after_v031_first_run": len(baseline["work"]["unscheduled"]),
    "re_registered_on_first_open_after_upgrade": True,
    "reboot_identity_before": baseline["boot_id"],
    "reboot_identity_after": reboot["boot_id"],
    "physical_device_or_arm64_signature_upgrade_verified": False,
}
(root / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
PY

cat "$RECOVERY_REPORT/summary.json"
echo "Android emulator recovery acceptance passed. Reports: $RECOVERY_REPORT"
