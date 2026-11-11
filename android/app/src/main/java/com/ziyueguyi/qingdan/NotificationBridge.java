package com.ziyueguyi.qingdan;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.os.Build;
public final class NotificationBridge {
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
