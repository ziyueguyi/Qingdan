package com.ziyueguyi.qingdan;

import android.content.BroadcastReceiver;
import android.content.Context;
import android.content.Intent;
import androidx.work.Data;
import androidx.work.ExistingWorkPolicy;
import androidx.work.OneTimeWorkRequest;
import androidx.work.WorkManager;
import org.json.JSONObject;
import java.util.concurrent.TimeUnit;

/** Explicit immutable PendingIntents only. Local work must not require network. */
public final class NotificationActionReceiver extends BroadcastReceiver {
    @Override public void onReceive(Context context,Intent intent){
        String raw=intent.getStringExtra(NotificationBridge.PAYLOAD);
        String action=intent.getStringExtra("notification_action");
        if(raw==null||raw.length()>4096||!("complete".equals(action)||"snooze".equals(action)))return;
        try{
            JSONObject payload=new JSONObject(raw);
            if(!"task".equals(payload.getString("kind"))||payload.optJSONObject("expected_rev")==null)return;
            Data data=new Data.Builder().putString("payload",raw).putString("action",action).build();
            OneTimeWorkRequest request=new OneTimeWorkRequest.Builder(NotificationActionWorker.class).setInputData(data).addTag("notification-action:"+payload.getString("item_id")).build();
            String key="notification:"+intent.getDataString();
            PendingResult pending=goAsync();
            new Thread(()->{
                try{WorkManager.getInstance(context).enqueueUniqueWork(key,ExistingWorkPolicy.KEEP,request).getResult().get(8,TimeUnit.SECONDS);}
                catch(Exception error){android.util.Log.e("Qingdan","Notification action was not persisted",error);NotificationBridge.finishAction(raw,"{\"ok\":false}");}
                finally{pending.finish();}
            },"qingdan-notification-enqueue").start();
        }catch(Exception error){android.util.Log.e("Qingdan","Notification action could not be queued",error);}
    }
}
