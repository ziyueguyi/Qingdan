package com.ziyueguyi.qingdan;

import android.app.Notification;
import android.app.NotificationManager;
import android.app.UiAutomation;
import android.content.Context;
import android.net.ConnectivityManager;
import android.os.Build;
import android.os.ParcelFileDescriptor;
import android.service.notification.StatusBarNotification;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.work.*;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import static org.junit.Assert.*;
import static org.junit.Assume.assumeTrue;
import java.io.ByteArrayOutputStream;
import java.io.InputStream;
import java.nio.charset.StandardCharsets;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;
import java.util.concurrent.TimeUnit;

@RunWith(AndroidJUnit4.class)
public class AndroidOfflineAlertsTest {
    private JSONObject call(Context context,String name,JSONObject params) throws Exception {
        JSONObject response=new JSONObject(RuntimeBridge.invoke(context,name,params.toString()));
        assertTrue(response.toString(),response.getBoolean("ok"));
        return response.getJSONObject("data");
    }

    private String shell(UiAutomation automation,String command) throws Exception {
        try(InputStream input=new ParcelFileDescriptor.AutoCloseInputStream(automation.executeShellCommand(command));
            ByteArrayOutputStream output=new ByteArrayOutputStream()) {
            byte[] buffer=new byte[1024];int length;
            while((length=input.read(buffer))!=-1)output.write(buffer,0,length);
            return new String(output.toByteArray(),StandardCharsets.UTF_8).trim();
        }
    }

    @Test public void localReminderWorkerPostsNotificationWithoutNetwork() throws Exception {
        // This test deliberately changes networking only on the disposable CI emulator.
        assumeTrue("Network isolation test requires an emulator",Build.HARDWARE.equals("ranchu")||Build.HARDWARE.equals("goldfish"));
        Context context=InstrumentationRegistry.getInstrumentation().getTargetContext();
        UiAutomation automation=InstrumentationRegistry.getInstrumentation().getUiAutomation();
        if(Build.VERSION.SDK_INT>=33)automation.grantRuntimePermission(context.getPackageName(),android.Manifest.permission.POST_NOTIFICATIONS);
        JSONObject old=call(context,"alert.config_get",new JSONObject());
        JSONObject restore=new JSONObject().put("master",old.get("master")).put("channels",old.get("channels")).put("dnd",old.get("dnd"));
        String wifi=shell(automation,"settings get global wifi_on");
        String mobile=shell(automation,"settings get global mobile_data");
        String airplane=shell(automation,"cmd connectivity airplane-mode");
        assertTrue("Cannot inspect airplane mode: "+airplane,airplane.equals("enabled")||airplane.equals("disabled"));
        String itemId=null;
        OneTimeWorkRequest work=null;
        WorkManager manager=WorkManager.getInstance(context);
        try {
            // AOSP ConnectivityService shell interface; prevent emulator radio auto-reconnect.
            shell(automation,"cmd connectivity airplane-mode enable");
            assertEquals("enabled",shell(automation,"cmd connectivity airplane-mode"));
            shell(automation,"svc wifi disable");shell(automation,"svc data disable");
            ConnectivityManager connections=(ConnectivityManager)context.getSystemService(Context.CONNECTIVITY_SERVICE);
            for(int i=0;i<30&&connections.getActiveNetwork()!=null;i++)Thread.sleep(200);
            assertNull("Emulator still has an active network",connections.getActiveNetwork());
            JSONObject config=new JSONObject().put("master",true).put("channels",new JSONObject().put("toast",true).put("dingtalk",false).put("email",false)).put("dnd",new JSONObject().put("enabled",false));
            call(context,"alert.config_update",new JSONObject().put("config",config));
            String title="Offline alert "+System.currentTimeMillis();
            String due=new SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss",Locale.ROOT).format(new Date(System.currentTimeMillis()-10000));
            itemId=call(context,"task.create",new JSONObject().put("title",title).put("due_at",due)).getString("id");
            work=new OneTimeWorkRequest.Builder(AlertWorker.class).build();
            manager.enqueue(work).getResult().get(10,TimeUnit.SECONDS);
            WorkInfo info=null;
            for(int i=0;i<90;i++){
                info=manager.getWorkInfoById(work.getId()).get(5,TimeUnit.SECONDS);
                if(info!=null&&info.getState().isFinished())break;
                Thread.sleep(500);
            }
            assertNotNull(info);assertEquals(info.toString(),WorkInfo.State.SUCCEEDED,info.getState());
            NotificationManager notifications=(NotificationManager)context.getSystemService(Context.NOTIFICATION_SERVICE);
            boolean received=false;
            for(int i=0;i<20&&!received;i++){
                for(StatusBarNotification notification:notifications.getActiveNotifications()){
                    CharSequence text=notification.getNotification().extras.getCharSequence(Notification.EXTRA_TEXT);
                    if(text!=null&&text.toString().contains(title))received=true;
                }
                if(!received)Thread.sleep(250);
            }
            assertTrue("Offline local worker did not post the due task notification",received);
            assertEquals("enabled",shell(automation,"cmd connectivity airplane-mode"));
            android.net.Network active=connections.getActiveNetwork();
            assertNull("Network returned during offline test; capabilities="+
                (active==null?"none":connections.getNetworkCapabilities(active)),active);
        } finally {
            try {
                if(work!=null)manager.cancelWorkById(work.getId());
                if(itemId!=null)call(context,"task.delete",new JSONObject().put("item_id",itemId));
                call(context,"alert.config_update",new JSONObject().put("config",restore));
            } finally {
                shell(automation,"cmd connectivity airplane-mode "+(airplane.equals("enabled")?"enable":"disable"));
                shell(automation,"svc wifi "+(wifi.equals("1")?"enable":"disable"));
                shell(automation,"svc data "+(mobile.equals("1")?"enable":"disable"));
            }
        }
    }
}
