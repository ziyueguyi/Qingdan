package com.ziyueguyi.qingdan;

import android.content.Context;
import androidx.work.Worker;
import androidx.work.WorkerParameters;

/** Local reminders must still be scanned when there is no network. */
public class AlertWorker extends Worker {
    public AlertWorker(Context context, WorkerParameters parameters) { super(context, parameters); }

    @Override public Result doWork() {
        try {
            return WorkerOutcome.fromResponse(RuntimeBridge.invoke(getApplicationContext(), "alert.tick", "{}"));
        } catch (Exception startupError) {
            return Result.retry();
        }
    }
}
