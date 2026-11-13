package com.ziyueguyi.qingdan;

import android.app.Notification;
import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.service.notification.StatusBarNotification;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.work.WorkInfo;
import androidx.work.WorkManager;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.util.List;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class AndroidNotificationActionsTest {
    private Context context(){return InstrumentationRegistry.getInstrumentation().getTargetContext();}
    private JSONObject call(String name,JSONObject params) throws Exception {
        JSONObject result=new JSONObject(RuntimeBridge.invoke(context(),name,params.toString()));
        assertTrue(result.toString(),result.getBoolean("ok"));return result.getJSONObject("data");
    }
    private JSONObject task(String id) throws Exception {return call("task.get",new JSONObject().put("item_id",id));}
    private JSONObject payload(JSONObject task,long fire) throws Exception {
        String[] revision=task.getString("rev").split("\\.",3);
        return new JSONObject().put("item_id",task.getString("id")).put("kind","task").put("fire_at",fire)
            .put("expected_rev",new JSONObject().put("ms",Long.parseLong(revision[0])).put("seq",Long.parseLong(revision[1])).put("dev",revision[2]));
    }
    private Notification notification(JSONObject payload) throws Exception {
        for(int attempt=0;attempt<40;attempt++){
            for(StatusBarNotification item:context().getSystemService(NotificationManager.class).getActiveNotifications())
                if(NotificationBridge.tag(payload).equals(item.getTag())&&payload.getJSONObject("expected_rev").toString().equals(item.getNotification().extras.getString(NotificationBridge.SNAPSHOT)))return item.getNotification();
            Thread.sleep(50); // NotificationManager enqueues system work asynchronously.
        }
        return null;
    }
    private WorkInfo finished(String id,java.util.UUID previous) throws Exception {
        List<WorkInfo> items=null;
        for(int i=0;i<120;i++){
            items=WorkManager.getInstance(context()).getWorkInfosByTag("notification-action:"+id).get(5,TimeUnit.SECONDS);
            for(WorkInfo item:items)
                if(item.getState().isFinished()&&!item.getId().equals(previous))return item;
            Thread.sleep(250);
        }
        fail("Notification work did not finish: "+items);return null;
    }
    @Test public void buttonsBindSnapshotsAndColdStartOpensTask() throws Exception {
        if(Build.VERSION.SDK_INT>=33)InstrumentationRegistry.getInstrumentation().getUiAutomation().grantRuntimePermission(context().getPackageName(),android.Manifest.permission.POST_NOTIFICATIONS);
        JSONObject first=call("task.create",new JSONObject().put("title","Notification repeat target").put("due_at","2030-01-01").put("repeat","daily"));
        JSONObject second=call("task.create",new JSONObject().put("title","Notification snooze target").put("due_at","2030-01-01"));
        String firstId=first.getString("id"),secondId=second.getString("id");
        JSONObject a=payload(first,System.currentTimeMillis()),b=payload(second,System.currentTimeMillis());
        try{
            assertTrue(NotificationBridge.postItem("Repeat","Test",a.toString()));
            assertTrue(NotificationBridge.postItem("Snooze","Test",b.toString()));
            Notification na=notification(a),nb=notification(b);
            assertNotNull(na);assertNotNull(nb);assertEquals(2,na.actions.length);assertEquals(2,nb.actions.length);
            assertNotEquals(na.actions[0].actionIntent,nb.actions[0].actionIntent);
            na.actions[0].actionIntent.send();
            WorkInfo firstJob=finished(firstId,null);
            assertEquals(WorkInfo.State.SUCCEEDED,firstJob.getState());
            assertEquals("completed",firstJob.getOutputData().getString("status"));
            assertEquals("2030-01-02",task(firstId).getJSONObject("data").getString("due_at"));
            JSONObject newer=payload(task(firstId),a.getLong("fire_at"));
            assertTrue(NotificationBridge.postItem("New occurrence","Test",newer.toString()));
            na.actions[0].actionIntent.send();
            WorkInfo repeated=finished(firstId,firstJob.getId());
            assertEquals("stale",repeated.getOutputData().getString("status"));
            assertEquals("2030-01-02",task(firstId).getJSONObject("data").getString("due_at"));
            assertEquals("Old action replaced the newer notification",2,notification(newer).actions.length);
            nb.actions[1].actionIntent.send();
            assertEquals("snoozed",finished(secondId,null).getOutputData().getString("status"));
            assertEquals("2030-01-01",task(secondId).getJSONObject("data").getString("due_at"));
            assertTrue(task(secondId).getJSONObject("data").has("snoozed_until"));
            // A fresh Activity receives an OS notification intent before JS boots.
            // A caller-supplied action must remain navigation-only.
            Intent open=new Intent(context(),MainActivity.class).putExtra(NotificationBridge.PAYLOAD,b.toString()).putExtra("notification_action","complete");
            try(ActivityScenario<MainActivity> scenario=ActivityScenario.launch(open)){
                // A navigation discards evaluateJavascript callbacks from the
                // initial blank document. Wait on native load state first;
                // the assertions below still require real JS task selection.
                AtomicReference<Boolean> loaded=new AtomicReference<>(false);
                for(int i=0;i<120&&!loaded.get();i++){
                    scenario.onActivity(activity->{
                        android.webkit.WebView view=activity.getWebView();
                        loaded.set(view.getProgress()==100&&view.getUrl()!=null&&view.getUrl().endsWith("/index.html"));
                    });
                    if(!loaded.get())Thread.sleep(500);
                }
                assertTrue("Notification Activity document did not finish loading",loaded.get());
                boolean selected=false;
                for(int i=0;i<100&&!selected;i++){
                    selected="true".equals(js(scenario,"Boolean(document.querySelector('#detail [data-id=\""+secondId+"\"]'))&&document.getElementById('app').classList.contains('phone-detail')"));
                    if(!selected)Thread.sleep(300);
                }
                assertTrue("Cold-start notification did not open the task detail",selected);
                assertEquals("External Activity extras caused a mutation","todo",task(secondId).getJSONObject("data").getString("status"));
            }
        }finally{
            call("task.delete",new JSONObject().put("item_id",firstId));
            call("task.delete",new JSONObject().put("item_id",secondId));
            NotificationManager manager=context().getSystemService(NotificationManager.class);
            manager.cancel(NotificationBridge.tag(a),0);manager.cancel(NotificationBridge.tag(b),0);
        }
    }
    private String js(ActivityScenario<MainActivity> scenario,String script) throws Exception {
        AtomicReference<String> value=new AtomicReference<>();CountDownLatch done=new CountDownLatch(1);
        scenario.onActivity(activity->activity.getWebView().evaluateJavascript(script,result->{value.set(result);done.countDown();}));
        assertTrue(done.await(10,TimeUnit.SECONDS));return value.get();
    }
}
