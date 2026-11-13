package com.ziyueguyi.qingdan;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.os.Build;
import android.net.Uri;
import android.service.notification.StatusBarNotification;
import org.json.JSONObject;
public final class NotificationBridge {
    public static final String PAYLOAD="qingdan_notification", SNAPSHOT="qingdan_snapshot";
    public static String tag(JSONObject payload){return "item:"+payload.optString("item_id")+":"+payload.optLong("fire_at");}
    public static boolean postItem(String title,String body,String raw){
        try{return show(title,body,new JSONObject(raw),null);}catch(Exception error){return false;}
    }
    private static PendingIntent intent(Context context,JSONObject payload,String action) throws Exception {
        JSONObject revision=payload.optJSONObject("expected_rev");
        Uri.Builder identity=new Uri.Builder().scheme("qingdan").authority("notification")
            .appendPath(payload.getString("item_id")).appendPath(String.valueOf(payload.optLong("fire_at")))
            .appendPath(revision==null?"none":revision.toString()).appendPath(action);
        Intent value=new Intent(context,"open".equals(action)?MainActivity.class:NotificationActionReceiver.class)
            .setData(identity.build()).putExtra(PAYLOAD,payload.toString()).putExtra("notification_action",action);
        if("open".equals(action)){
            value.addFlags(Intent.FLAG_ACTIVITY_CLEAR_TOP|Intent.FLAG_ACTIVITY_SINGLE_TOP);
            return PendingIntent.getActivity(context,0,value,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        }
        return PendingIntent.getBroadcast(context,0,value,PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
    }
    private static boolean show(String title,String body,JSONObject payload,String feedback) throws Exception {
        Context context=QingdanApplication.get();
        if(Build.VERSION.SDK_INT>=33&&context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)return false;
        NotificationManager manager=context.getSystemService(NotificationManager.class);
        if(!manager.areNotificationsEnabled())return false;
        if(Build.VERSION.SDK_INT>=26)manager.createNotificationChannel(new NotificationChannel("tasks","清单提醒",NotificationManager.IMPORTANCE_DEFAULT));
        Notification.Builder builder=(Build.VERSION.SDK_INT>=26?new Notification.Builder(context,"tasks"):new Notification.Builder(context)).setSmallIcon(R.drawable.ic_launcher)
            .setContentTitle(title).setContentText(body).setStyle(new Notification.BigTextStyle().bigText(body))
            .setAutoCancel(true).setContentIntent(intent(context,payload,"open"));
        android.os.Bundle extras=new android.os.Bundle();
        extras.putString(SNAPSHOT,payload.optJSONObject("expected_rev")==null?"":payload.getJSONObject("expected_rev").toString());
        builder.addExtras(extras);
        if(feedback==null&&"task".equals(payload.optString("kind"))&&payload.optJSONObject("expected_rev")!=null){
            builder.addAction(new Notification.Action.Builder(null,"完成",intent(context,payload,"complete")).build());
            builder.addAction(new Notification.Action.Builder(null,"稍后 10 分钟",intent(context,payload,"snooze")).build());
        }
        manager.notify(tag(payload),0,builder.build());return true;
    }
    public static void finishAction(String raw,String response){
        try{
            JSONObject payload=new JSONObject(raw),reply=new JSONObject(response);
            NotificationManager manager=QingdanApplication.get().getSystemService(NotificationManager.class);
            String snapshot=payload.getJSONObject("expected_rev").toString();
            boolean current=false;
            for(StatusBarNotification notification:manager.getActiveNotifications()){
                if(tag(payload).equals(notification.getTag())&&snapshot.equals(notification.getNotification().extras.getString(SNAPSHOT)))current=true;
            }
            if(!current)return; // Never cancel or replace a newer notification.
            String status=reply.optBoolean("ok")?reply.getJSONObject("data").optString("status"):"error";
            if("completed".equals(status)||"snoozed".equals(status)||"gone".equals(status)||"inactive".equals(status))manager.cancel(tag(payload),0);
            else show("清单提醒","stale".equals(status)?"任务已变化，请打开查看":"操作未完成，请打开应用查看",payload,status);
        }catch(Exception error){android.util.Log.e("Qingdan","Notification result could not be displayed",error);}
    }
    public static boolean post(String title,String body){
        Context context=QingdanApplication.get();
        if(Build.VERSION.SDK_INT>=33&&context.checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)return false;
        NotificationManager manager=(NotificationManager)context.getSystemService(Context.NOTIFICATION_SERVICE);
        if(Build.VERSION.SDK_INT>=26)manager.createNotificationChannel(new NotificationChannel("tasks","清单提醒",NotificationManager.IMPORTANCE_DEFAULT));
        PendingIntent open=PendingIntent.getActivity(context,0,new Intent(context,MainActivity.class),PendingIntent.FLAG_UPDATE_CURRENT|PendingIntent.FLAG_IMMUTABLE);
        Notification.Builder builder=Build.VERSION.SDK_INT>=26?new Notification.Builder(context,"tasks"):new Notification.Builder(context);
        manager.notify((title+body).hashCode(),builder.setSmallIcon(R.drawable.ic_launcher).setContentTitle(title).setContentText(body).setStyle(new Notification.BigTextStyle().bigText(body)).setAutoCancel(true).setContentIntent(open).build());
        return true;
    }
}
