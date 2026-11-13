package com.ziyueguyi.qingdan;

import android.app.NotificationManager;
import android.content.Context;
import android.content.Intent;
import android.os.Build;
import android.os.ParcelFileDescriptor;
import android.service.notification.StatusBarNotification;
import androidx.test.core.app.ActivityScenario;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import androidx.test.platform.app.InstrumentationRegistry;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.util.UUID;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;
import static org.junit.Assert.*;
import static org.junit.Assume.assumeTrue;

@RunWith(AndroidJUnit4.class)
public class AndroidNotificationInboxTest {
    private Context context(){return InstrumentationRegistry.getInstrumentation().getTargetContext();}
    private void python(String source){
        com.chaquo.python.Python.getInstance().getModule("builtins").callAttr("exec",source,
            com.chaquo.python.Python.getInstance().getModule("builtins").callAttr("dict"));
    }
    @Test public void tenThousandRemindersUseOneSummaryAndBoundedUi() throws Exception {
        assumeTrue("Large fixture and screenshot require a disposable emulator",Build.HARDWARE.equals("ranchu")||Build.HARDWARE.equals("goldfish"));
        if(Build.VERSION.SDK_INT>=33)InstrumentationRegistry.getInstrumentation().getUiAutomation().grantRuntimePermission(context().getPackageName(),android.Manifest.permission.POST_NOTIFICATIONS);
        JSONObject ready=new JSONObject(RuntimeBridge.invoke(context(),"app.health","{}"));assertTrue(ready.toString(),ready.getBoolean("ok"));
        String prefix="NATIVEINBOX"+UUID.randomUUID().toString();
        try{
            python("prefix="+JSONObject.quote(prefix)+"\n"+"""
                import json
                from qingdan.core.context import ctx
                from qingdan.core.alerting.base import AlertMessage,get_channel
                with ctx.store.operation_lock:
                    rows=[]
                    messages=[]
                    for i in range(10000):
                        item_id=prefix+str(i).zfill(5)
                        title='Native reminder '+str(i).zfill(5)
                        data={'title':title,'status':'todo','due_at':'2030-01-01','priority':4,'tags':[],'order':i}
                        rows.append((item_id,json.dumps(data)))
                        messages.append(AlertMessage(item_id=item_id,kind='task',title=title,fire_at_ms=1000,
                            revision={'ms':1,'seq':0,'dev':'native-inbox-test'}))
                    ctx.store.db.executemany("INSERT INTO item(id,kind,rev_ms,rev_seq,rev_dev,data,updated_at) VALUES(?,'task',1,0,'native-inbox-test',?,1)",rows)
                    ctx.store.db.commit()
                    result=get_channel('toast').send(messages)
                    assert result.ok and result.sent_count==1 and result.covered==10000, result
                """);
            NotificationManager manager=context().getSystemService(NotificationManager.class);
            boolean summary=false;
            for(int attempt=0;attempt<40&&!summary;attempt++){
                int count=0;
                for(StatusBarNotification item:manager.getActiveNotifications()){
                    if("item:__inbox__:0".equals(item.getTag()))summary=true;
                    if(item.getTag()!=null&&item.getTag().startsWith("item:"))count++;
                }
                assertTrue("Too many platform notifications: "+count,count<=21);
                if(!summary)Thread.sleep(50);
            }
            assertTrue("Summary notification missing",summary);
            JSONObject inbox=new JSONObject(RuntimeBridge.invoke(context(),"alert.notification_list","{\"limit\":50}"));
            assertTrue(inbox.toString(),inbox.getBoolean("ok"));
            int total=inbox.getJSONObject("data").getInt("total");assertTrue(total>=10000);
            Intent open=new Intent(context(),MainActivity.class).putExtra(NotificationBridge.PAYLOAD,"{\"inbox\":true}");
            try(ActivityScenario<MainActivity> scenario=ActivityScenario.launch(open)){
                await(scenario,"document.querySelectorAll('.notification-event').length===50&&document.querySelector('#modal .task-pager')?.textContent.includes('1–50')");
                assertEquals("true",js(scenario,"document.querySelector('#modal .task-pager').textContent.includes('"+total+"')"));
                js(scenario,"document.querySelector('[data-act=notification-page][data-v=\"1\"]').click();true");
                await(scenario,"document.querySelector('#modal .task-pager')?.textContent.includes('51–100')&&document.querySelectorAll('.notification-event').length===50");
                js(scenario,"document.querySelector('.notification-events').scrollTop=document.querySelector('.notification-events').scrollHeight;true");
                await(scenario,"document.querySelector('#modal .task-pager')?.textContent.includes('101–150')&&document.querySelectorAll('.notification-event').length===50");
                // Shell-owned Download file survives AGP uninstalling the test app.
                try(ParcelFileDescriptor.AutoCloseInputStream output=new ParcelFileDescriptor.AutoCloseInputStream(
                    InstrumentationRegistry.getInstrumentation().getUiAutomation().executeShellCommand("screencap -p /sdcard/Download/qingdan-notification-inbox.png"))){
                    byte[] buffer=new byte[1024];while(output.read(buffer)!=-1){}
                }
                js(scenario,"document.querySelector('#modal [data-act=close-modal]').click();document.querySelector('[data-view=inbox]').click();document.querySelector('[data-act=layout][data-v=list]').click();true");
                await(scenario,"document.querySelectorAll('#scroll .task').length===50");
                js(scenario,"window.__firstNativeTask=document.querySelector('#scroll .task').dataset.id;document.querySelector('[data-act=task-page][data-v=\"1\"]').click();true");
                await(scenario,"document.querySelectorAll('#scroll .task').length===50&&document.querySelector('#scroll .task').dataset.id!==window.__firstNativeTask");
                js(scenario,"document.querySelector('[data-act=layout][data-v=board]').click();true");
                await(scenario,"Boolean(document.querySelector('#scroll .board'))&&document.querySelectorAll('#scroll .board .card').length>0&&document.querySelectorAll('#scroll .board .card').length<=100");
            }
        }finally{
            python("prefix="+JSONObject.quote(prefix)+"\n"+"""
                from qingdan.core.context import ctx
                with ctx.store.operation_lock:
                    ctx.store.db.execute('DELETE FROM notification_item WHERE item_id LIKE ?', (prefix+'%',))
                    ctx.store.db.execute('DELETE FROM item WHERE id LIKE ?', (prefix+'%',))
                    ctx.store.db.commit()
                """);
            context().getSystemService(NotificationManager.class).cancel("item:__inbox__:0",0);
        }
    }
    private void await(ActivityScenario<MainActivity> scenario,String expression) throws Exception {
        for(int i=0;i<120;i++){if("true".equals(js(scenario,expression)))return;Thread.sleep(250);}
        fail("Native inbox condition failed: "+expression);
    }
    private String js(ActivityScenario<MainActivity> scenario,String script) throws Exception {
        AtomicReference<String> value=new AtomicReference<>();CountDownLatch done=new CountDownLatch(1);
        scenario.onActivity(activity->activity.getWebView().evaluateJavascript(script,result->{value.set(result);done.countDown();}));
        assertTrue(done.await(10,TimeUnit.SECONDS));return value.get();
    }
}
