package com.ziyueguyi.qingdan;

import android.content.Context;
import androidx.work.Worker;
import androidx.work.WorkerParameters;
import androidx.work.Data;
import org.json.JSONObject;

/** Durable offline mutation. Core rejects snapshots from earlier occurrences. */
public final class NotificationActionWorker extends Worker {
    public NotificationActionWorker(Context context,WorkerParameters parameters){super(context,parameters);}
    @Override public Result doWork(){
        String raw=getInputData().getString("payload"),action=getInputData().getString("action");
        try{
            if(raw==null||!("complete".equals(action)||"snooze".equals(action)))return Result.failure();
            JSONObject payload=new JSONObject(raw);
            JSONObject params=new JSONObject().put("item_id",payload.getString("item_id")).put("action",action)
                .put("expected_rev",payload.getJSONObject("expected_rev")).put("minutes",10);
            String response=RuntimeBridge.invoke(getApplicationContext(),"alert.notification_action",params.toString());
            NotificationBridge.finishAction(raw,response);
            JSONObject result=new JSONObject(response);
            return result.optBoolean("ok")?Result.success(new Data.Builder().putString("status",result.getJSONObject("data").optString("status")).build()):Result.failure();
        }catch(Exception startupError){
            if(getRunAttemptCount()<2)return Result.retry();
            NotificationBridge.finishAction(raw,"{\"ok\":false}");
            return Result.failure();
        }
    }
}
