package com.ziyueguyi.qingdan;

import android.app.KeyguardManager;
import android.app.Notification;
import android.app.NotificationManager;
import android.content.Context;
import android.database.Cursor;
import android.database.sqlite.SQLiteDatabase;
import android.graphics.Bitmap;
import android.os.Build;
import android.os.ParcelFileDescriptor;
import android.os.PowerManager;
import android.service.notification.StatusBarNotification;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.work.OneTimeWorkRequest;
import androidx.work.WorkInfo;
import androidx.work.WorkManager;
import java.io.File;
import java.io.FileOutputStream;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.List;
import java.util.Locale;
import java.util.UUID;
import org.json.JSONArray;
import org.json.JSONObject;
import org.junit.Before;
import org.junit.Test;
import org.junit.runner.RunWith;

import static org.junit.Assert.*;
import static org.junit.Assume.assumeTrue;

/** Dedicated emulator-only acceptance stages, orchestrated by verify_android_recovery.sh. */
@RunWith(AndroidJUnit4.class)
public class AndroidDeviceRecoveryTest {
    private static final String PACKAGE = "com.ziyueguyi.qingdan";
    private static final String MARKER_TITLE = "QD-RECOVERY-V031-7F3A9C";
    private static final String MARKER_NOTE = "SQLite identity must survive overwrite install and adb reboot: 51c8e2a4";

    @Before public void dedicatedRecoveryInvocation() {
        assumeTrue("Run only through scripts/verify_android_recovery.sh",
            "true".equals(InstrumentationRegistry.getArguments().getString("recovery_stage")));
    }

    @Test public void prepareBaseline() throws Throwable {
        requireAndroid17Emulator();
        JSONObject evidence = baseEvidence("baseline");
        boolean passed = false;
        try {
            assertEquals("The baseline application must be built from published v0.3.1", "0.3.1", versionName());
            grantNotifications();
            // MainActivity is the production registration path for both periodic workers.
            try (ActivityScenario<MainActivity> ignored = ActivityScenario.launch(MainActivity.class)) {
                Thread.sleep(4000);
            }
            JSONObject task = call("task.create", new JSONObject()
                .put("title", MARKER_TITLE).put("note", MARKER_NOTE)
                .put("due_at", "2037-01-31T09:00:00").toString());
            String id = task.getString("id");
            JSONObject row = taskRow(id);
            evidence.put("task_id", id).put("title", MARKER_TITLE).put("note", MARKER_NOTE)
                .put("row", row).put("work", requirePeriodicWork());
            passed = true;
        } finally {
            finish(evidence, "baseline.json", passed);
        }
    }

    @Test public void verifyUpgrade() throws Throwable {
        requireAndroid17Emulator();
        JSONObject evidence = baseEvidence("upgrade");
        boolean passed = false;
        try {
            assertExpectedCurrentVersion();
            JSONObject baseline = readJson("baseline.json");
            assertEquals("passed", baseline.getString("status"));
            assertEquals("0.3.1", baseline.getString("version_name"));
            JSONObject row = verifyMarkerRow(baseline);
            evidence.put("task_id", baseline.getString("task_id")).put("baseline_row", baseline.getJSONObject("row"))
                .put("row", row).put("sqlite_verified", true);
            passed = true;
        } finally {
            finish(evidence, "upgrade.json", passed);
        }
    }

    @Test public void alertWorkerUnderLockAndPowerSaver() throws Throwable {
        requireAndroid17Emulator();
        JSONObject evidence = baseEvidence("locked_power_saver_alert");
        PowerManager power = context().getSystemService(PowerManager.class);
        KeyguardManager keyguard = context().getSystemService(KeyguardManager.class);
        boolean originalSaver = power.isPowerSaveMode();
        boolean originalInteractive = power.isInteractive();
        assertTrue("CI emulator must start with the screen on", originalInteractive);
        assertFalse("CI emulator must start unlocked", keyguard.isKeyguardLocked());
        grantNotifications();
        String taskId = null;
        UUID workId = null;
        boolean passed = false;
        try {
            // CI may run during the default 23:00-07:30 quiet hours; isolate this acceptance from DND.
            JSONObject config = call("alert.config_update", new JSONObject().put("config", new JSONObject().put("master", true)
                .put("silence_until", 0).put("channels", new JSONObject().put("toast", true))
                .put("dnd", new JSONObject().put("enabled", false))).toString());
            assertFalse(config.getJSONObject("dnd").getBoolean("enabled"));
            String due = new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.US)
                .format(new Date(System.currentTimeMillis() - 30_000));
            String title = "QD-LOCKED-ALERT-" + System.currentTimeMillis();
            JSONObject task = call("task.create", new JSONObject().put("title", title)
                .put("note", "AlertWorker emulator lock/power-saver acceptance").put("due_at", due).toString());
            taskId = task.getString("id");
            call("task.set_reminder", new JSONObject().put("item_id", taskId)
                .put("remind_at", new JSONArray()).put("mode", "once")
                .put("channels", new JSONArray().put("toast")).toString());
            JSONObject rebuilt = call("alert.rebuild", "{}");
            assertTrue("A due local alert job must be rebuilt: " + rebuilt, rebuilt.getInt("jobs") >= 1);

            shell("cmd power set-mode 1");
            await("power saver was not enabled", () -> power.isPowerSaveMode(), 30);
            shell("input keyevent KEYCODE_SLEEP");
            await("screen did not turn off", () -> !power.isInteractive(), 30);
            await("keyguard did not lock", keyguard::isKeyguardLocked, 30);
            evidence.put("locked_power_save", power.isPowerSaveMode())
                .put("locked_interactive", power.isInteractive())
                .put("locked_keyguard", keyguard.isKeyguardLocked());
            writeText("locked-power.txt", shell("dumpsys power") + "\n--- deviceidle ---\n" + shell("dumpsys deviceidle"));

            OneTimeWorkRequest request = new OneTimeWorkRequest.Builder(AlertWorker.class).build();
            workId = request.getId();
            WorkManager.getInstance(context()).enqueue(request);
            awaitWorkSucceeded(workId);
            String alertTaskId = taskId;
            await("AlertWorker did not post the task notification", () -> notificationVisible(alertTaskId, title), 60);
            evidence.put("task_id", taskId).put("work_id", workId.toString())
                .put("alert_worker_succeeded", true).put("notification_active", true)
                .put("notification_tag_prefix", "item:" + taskId + ":");

            // Wake without dismissing the keyguard: the PNG must show the real locked UI.
            shell("input keyevent KEYCODE_WAKEUP");
            await("screen did not wake for lock-screen evidence", power::isInteractive, 30);
            assertTrue(keyguard.isKeyguardLocked());
            assertTrue(power.isPowerSaveMode());
            Thread.sleep(1500);
            screenshot("locked-alert.png");
            evidence.put("locked_screenshot", "locked-alert.png");
            passed = true;
        } finally {
            // Restoration is unconditional, including worker, notification, or screenshot failures.
            shell("cmd power set-mode " + (originalSaver ? "1" : "0"));
            if (originalInteractive) {
                shell("input keyevent KEYCODE_WAKEUP");
                await("screen did not wake during restoration", power::isInteractive, 30);
                shell("wm dismiss-keyguard");
            } else {
                shell("input keyevent KEYCODE_SLEEP");
            }
            await("power saver state was not restored", () -> power.isPowerSaveMode() == originalSaver, 30);
            await("screen state was not restored", () -> power.isInteractive() == originalInteractive, 30);
            if (originalInteractive) await("keyguard was not restored", () -> !keyguard.isKeyguardLocked(), 30);
            evidence.put("restored_power_save", power.isPowerSaveMode())
                .put("restored_interactive", power.isInteractive())
                .put("restored_keyguard", keyguard.isKeyguardLocked())
                .put("original_power_save", originalSaver).put("original_interactive", originalInteractive);
            writeText("restored-power.txt", shell("dumpsys power") + "\n--- deviceidle ---\n" + shell("dumpsys deviceidle"));
            if (originalInteractive) {
                screenshot("restored-screen.png");
                evidence.put("restored_screenshot", "restored-screen.png");
            }
            if (taskId != null) evidence.put("task_id", taskId);
            if (workId != null) evidence.put("work_id", workId.toString());
            finish(evidence, "alert.json", passed);
        }
    }

    @Test public void verifyReboot() throws Throwable {
        requireAndroid17Emulator();
        JSONObject evidence = baseEvidence("reboot");
        boolean passed = false;
        try {
            assertExpectedCurrentVersion();
            JSONObject baseline = readJson("baseline.json");
            String bootId = shell("getprop ro.boot.boot_id").trim();
            assertFalse("boot id must be available", bootId.isEmpty());
            assertNotEquals("adb reboot must create a new boot", baseline.getString("boot_id"), bootId);
            JSONObject row = verifyMarkerRow(baseline);
            evidence.put("task_id", baseline.getString("task_id")).put("baseline_row", baseline.getJSONObject("row"))
                .put("row", row).put("sqlite_verified", true).put("work", requirePeriodicWork());
            passed = true;
        } finally {
            finish(evidence, "reboot.json", passed);
        }
    }

    private android.app.Instrumentation instrumentation() { return InstrumentationRegistry.getInstrumentation(); }
    private Context context() { return instrumentation().getTargetContext(); }

    private void requireAndroid17Emulator() throws Exception {
        String qemu = shell("getprop ro.kernel.qemu").trim();
        String hardware = shell("getprop ro.hardware").toLowerCase(Locale.ROOT);
        String fingerprint = Build.FINGERPRINT.toLowerCase(Locale.ROOT);
        boolean emulator = "1".equals(qemu) || hardware.contains("goldfish") || hardware.contains("ranchu")
            || fingerprint.contains("emulator") || fingerprint.contains("generic");
        assertTrue("This destructive acceptance is emulator-only; hardware=" + hardware, emulator);
        assertEquals("The one-shot acceptance requires Android 17 / API 37", 37, Build.VERSION.SDK_INT);
    }

    private JSONObject baseEvidence(String phase) throws Exception {
        return new JSONObject().put("phase", phase).put("package_name", PACKAGE)
            .put("version_name", versionName()).put("sdk_int", Build.VERSION.SDK_INT)
            .put("fingerprint", Build.FINGERPRINT).put("hardware", Build.HARDWARE)
            .put("boot_id", shell("getprop ro.boot.boot_id").trim())
            .put("captured_at_ms", System.currentTimeMillis());
    }

    private String versionName() throws Exception {
        return context().getPackageManager().getPackageInfo(PACKAGE, 0).versionName;
    }

    private void assertExpectedCurrentVersion() {
        String expected = InstrumentationRegistry.getArguments().getString("expected_version");
        if (expected != null) assertEquals(expected, versionNameUnchecked());
        assertNotEquals("Overwrite install must not still report the baseline", "0.3.1", versionNameUnchecked());
    }

    private String versionNameUnchecked() {
        try { return versionName(); } catch (Exception error) { throw new RuntimeException(error); }
    }

    private JSONObject call(String name, String params) throws Exception {
        JSONObject result = new JSONObject(RuntimeBridge.invoke(context(), name, params));
        assertTrue(result.toString(), result.getBoolean("ok"));
        return result.getJSONObject("data");
    }

    private void grantNotifications() {
        if (Build.VERSION.SDK_INT >= 33) {
            instrumentation().getUiAutomation().grantRuntimePermission(PACKAGE, android.Manifest.permission.POST_NOTIFICATIONS);
        }
    }

    private File evidenceDir() {
        File root = context().getExternalFilesDir(null);
        assertNotNull("External app-specific evidence directory is unavailable", root);
        File directory = new File(root, "recovery");
        assertTrue("Cannot create evidence directory", directory.isDirectory() || directory.mkdirs());
        return directory;
    }

    private void finish(JSONObject evidence, String name, boolean passed) throws Exception {
        evidence.put("status", passed ? "passed" : "failed");
        writeJson(name, evidence);
    }

    private void writeJson(String name, JSONObject value) throws Exception {
        writeText(name, value.toString(2));
    }

    private JSONObject readJson(String name) throws Exception {
        return new JSONObject(new String(Files.readAllBytes(new File(evidenceDir(), name).toPath()), StandardCharsets.UTF_8));
    }

    private void writeText(String name, String value) throws Exception {
        try (FileOutputStream output = new FileOutputStream(new File(evidenceDir(), name))) {
            output.write(value.getBytes(StandardCharsets.UTF_8));
        }
    }

    private JSONObject taskRow(String id) throws Exception {
        File database = new File(context().getFilesDir(), "data/db.sqlite");
        assertTrue("Application SQLite database is missing: " + database, database.isFile());
        SQLiteDatabase db = SQLiteDatabase.openDatabase(database.getAbsolutePath(), null, SQLiteDatabase.OPEN_READONLY);
        try (Cursor cursor = db.rawQuery(
            "SELECT kind,deleted,data,rev_ms,rev_seq,rev_dev FROM item WHERE id=?", new String[]{id})) {
            assertEquals("Expected exactly one SQLite item row for " + id, 1, cursor.getCount());
            assertTrue(cursor.moveToFirst());
            return new JSONObject().put("id", id).put("kind", cursor.getString(0))
                .put("deleted", cursor.getInt(1)).put("data", new JSONObject(cursor.getString(2)))
                .put("rev_ms", cursor.getLong(3)).put("rev_seq", cursor.getLong(4))
                .put("rev_dev", cursor.getString(5)).put("database", database.getAbsolutePath());
        } finally {
            db.close();
        }
    }

    private JSONObject verifyMarkerRow(JSONObject baseline) throws Exception {
        JSONObject before = baseline.getJSONObject("row");
        JSONObject after = taskRow(baseline.getString("task_id"));
        JSONObject data = after.getJSONObject("data"), original = before.getJSONObject("data");
        assertEquals("task", after.getString("kind"));
        assertEquals("Marker task was deleted", 0, after.getInt("deleted"));
        assertEquals(MARKER_TITLE, data.getString("title"));
        assertEquals(MARKER_NOTE, data.getString("note"));
        assertEquals(original.getString("due_at"), data.getString("due_at"));
        assertEquals("SQLite revision milliseconds changed", before.getLong("rev_ms"), after.getLong("rev_ms"));
        assertEquals("SQLite revision sequence changed", before.getLong("rev_seq"), after.getLong("rev_seq"));
        assertEquals("SQLite revision device changed", before.getString("rev_dev"), after.getString("rev_dev"));
        return after;
    }

    private JSONObject requirePeriodicWork() throws Exception {
        JSONObject result = new JSONObject();
        JSONObject systemJobs = null;
        String jobDump = null;
        for (String name : new String[]{"qingdan-alerts", "qingdan-sync"}) {
            List<WorkInfo> infos = null;
            for (int attempt = 0; attempt < 60; attempt++) {
                infos = WorkManager.getInstance(context()).getWorkInfosForUniqueWork(name).get();
                if (!infos.isEmpty()) break;
                Thread.sleep(500);
            }
            assertNotNull(infos);
            assertFalse("Missing periodic WorkManager registration: " + name, infos.isEmpty());
            // The WorkManager future is the initialization barrier. Checking the
            // database filename before this point races asynchronous enqueue.
            if (systemJobs == null) {
                systemJobs = systemJobIds();
                jobDump = shell("dumpsys jobscheduler");
            }
            JSONArray entries = new JSONArray();
            for (WorkInfo info : infos) {
                assertNotEquals(name + " must not be cancelled", WorkInfo.State.CANCELLED, info.getState());
                assertTrue(name + " must remain periodic/enqueued or running: " + info.getState(),
                    info.getState() == WorkInfo.State.ENQUEUED || info.getState() == WorkInfo.State.RUNNING);
                String workId = info.getId().toString();
                assertTrue("Missing SystemIdInfo for " + name + "/" + workId, systemJobs.has(workId));
                int systemJobId = systemJobs.getInt(workId);
                assertSystemJob(jobDump, systemJobId, name);
                entries.put(new JSONObject().put("id", workId).put("system_job_id", systemJobId)
                    .put("state", info.getState().name()).put("run_attempt_count", info.getRunAttemptCount())
                    .put("tags", new JSONArray(info.getTags())));
            }
            result.put(name, entries);
        }
        return result;
    }

    private JSONObject systemJobIds() throws Exception {
        File database = new File(context().getNoBackupFilesDir(), "androidx.work.workdb");
        assertTrue("WorkManager database is missing: " + database, database.isFile());
        SQLiteDatabase db = SQLiteDatabase.openDatabase(database.getAbsolutePath(), null, SQLiteDatabase.OPEN_READONLY);
        JSONObject result = new JSONObject();
        try (Cursor cursor = db.rawQuery("SELECT work_spec_id,system_id FROM SystemIdInfo", null)) {
            while (cursor.moveToNext()) result.put(cursor.getString(0), cursor.getInt(1));
        } finally {
            db.close();
        }
        return result;
    }

    private void assertSystemJob(String dump, int systemJobId, String workName) {
        java.util.regex.Matcher matcher = java.util.regex.Pattern.compile(
            "(?ms)^\\s*JOB\\s+(?:[A-Za-z0-9_.-]+:)?#?\\S*/" + systemJobId + ":.*?(?=^\\s*JOB\\s|\\z)").matcher(dump);
        assertTrue("Missing OS JobScheduler block for " + workName + "/" + systemJobId, matcher.find());
        String block = matcher.group();
        assertTrue("Wrong package in OS job for " + workName + ": " + block, block.contains(PACKAGE));
        assertTrue("Wrong service in OS job for " + workName + ": " + block,
            block.contains("androidx.work.impl.background.systemjob.SystemJobService"));
    }

    private void awaitWorkSucceeded(UUID id) throws Exception {
        WorkInfo.State state = null;
        for (int attempt = 0; attempt < 120; attempt++) {
            WorkInfo info = WorkManager.getInstance(context()).getWorkInfoById(id).get();
            assertNotNull("AlertWorker request disappeared", info);
            state = info.getState();
            if (state == WorkInfo.State.SUCCEEDED) return;
            assertTrue("AlertWorker failed: " + state,
                state == WorkInfo.State.ENQUEUED || state == WorkInfo.State.RUNNING || state == WorkInfo.State.BLOCKED);
            Thread.sleep(1000);
        }
        fail("AlertWorker did not finish within 120 seconds; last state=" + state);
    }

    private boolean notificationVisible(String taskId, String title) {
        NotificationManager manager = context().getSystemService(NotificationManager.class);
        for (StatusBarNotification posted : manager.getActiveNotifications()) {
            if (!PACKAGE.equals(posted.getPackageName()) || posted.getTag() == null
                || !posted.getTag().startsWith("item:" + taskId + ":")) continue;
            CharSequence actual = posted.getNotification().extras.getCharSequence(Notification.EXTRA_TITLE);
            if (title.contentEquals(actual)) return true;
        }
        return false;
    }

    private interface Condition { boolean evaluate() throws Exception; }
    private void await(String message, Condition condition, int seconds) throws Exception {
        for (int attempt = 0; attempt < seconds * 2; attempt++) {
            if (condition.evaluate()) return;
            Thread.sleep(500);
        }
        assertTrue(message, condition.evaluate());
    }

    private String shell(String command) throws Exception {
        ParcelFileDescriptor descriptor = instrumentation().getUiAutomation().executeShellCommand(command);
        try (ParcelFileDescriptor.AutoCloseInputStream input = new ParcelFileDescriptor.AutoCloseInputStream(descriptor)) {
            java.io.ByteArrayOutputStream output = new java.io.ByteArrayOutputStream();
            byte[] buffer = new byte[8192];
            int read;
            while ((read = input.read(buffer)) != -1) output.write(buffer, 0, read);
            return output.toString(StandardCharsets.UTF_8.name());
        }
    }

    private void screenshot(String name) throws Exception {
        Bitmap bitmap = instrumentation().getUiAutomation().takeScreenshot();
        assertNotNull("Real device screenshot is unavailable: " + name, bitmap);
        try (FileOutputStream output = new FileOutputStream(new File(evidenceDir(), name))) {
            assertTrue("PNG encoding failed", bitmap.compress(Bitmap.CompressFormat.PNG, 100, output));
        } finally {
            bitmap.recycle();
        }
    }
}
