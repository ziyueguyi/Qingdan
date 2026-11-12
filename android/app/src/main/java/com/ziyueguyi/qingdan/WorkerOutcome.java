package com.ziyueguyi.qingdan;

import androidx.work.Data;
import androidx.work.ListenableWorker;
import org.json.JSONObject;
import java.util.Set;
import java.util.Arrays;
import java.util.HashSet;

/** Translate the bridge's JSON envelope: a completed JNI call may still fail. */
final class WorkerOutcome {
    private static final Set<String> ACTION_REQUIRED = new HashSet<>(Arrays.asList(
        "AUTH_REQUIRED", "TOKEN_INVALID", "API_TOKEN_EXPIRED", "REPO_PUBLIC",
        "VALIDATION_ERROR", "CLOCK_SKEW", "clock_skew", "PLATFORM_UNSUPPORTED"));

    static ListenableWorker.Result fromResponse(String response) {
        try {
            JSONObject envelope = new JSONObject(response);
            String code = null;
            if (!envelope.getBoolean("ok")) {
                code = envelope.getJSONObject("error").getString("code");
            } else {
                JSONObject data = envelope.optJSONObject("data");
                if (data != null && data.has("ok") && !data.getBoolean("ok"))
                    code = data.optString("error_code", "SYNC_FAILED");
            }
            if (code == null) return ListenableWorker.Result.success();
            if (ACTION_REQUIRED.contains(code))
                return ListenableWorker.Result.failure(new Data.Builder().putString("error_code", code).build());
            return ListenableWorker.Result.retry();
        } catch (Exception malformedResponse) {
            return ListenableWorker.Result.retry();
        }
    }
}
