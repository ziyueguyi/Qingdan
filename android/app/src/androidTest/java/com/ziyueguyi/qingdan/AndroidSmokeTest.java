package com.ziyueguyi.qingdan;
import android.content.Context;
import android.os.Build;
import androidx.test.core.app.ActivityScenario;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import org.junit.Test;
import org.junit.runner.RunWith;
import org.json.JSONObject;
import static org.junit.Assert.*;
import java.util.concurrent.CountDownLatch;
import java.util.concurrent.TimeUnit;
import java.util.concurrent.atomic.AtomicReference;

@RunWith(AndroidJUnit4.class)
public class AndroidSmokeTest {
    private Context context(){return InstrumentationRegistry.getInstrumentation().getTargetContext();}
    private JSONObject call(String name,String params) throws Exception {
        JSONObject result=new JSONObject(RuntimeBridge.invoke(context(),name,params));
        assertTrue(result.toString(),result.getBoolean("ok"));return result.getJSONObject("data");
    }
    @Test public void offlineCrudKeystoreAndUi() throws Exception {
        if(Build.VERSION.SDK_INT>=33)InstrumentationRegistry.getInstrumentation().getUiAutomation().grantRuntimePermission(context().getPackageName(),android.Manifest.permission.POST_NOTIFICATIONS);
        JSONObject group=call("group.create","{\"name\":\"Android list\",\"icon\":\"⭐\"}");
        JSONObject task=call("task.create",new JSONObject().put("title","Android smoke task").put("list_id",group.getString("id")).put("due_at","2027-01-31T09:00:00").put("repeat","monthly").toString());
        JSONObject advanced=call("task.complete",new JSONObject().put("item_id",task.getString("id")).toString());
        assertEquals("2027-02-28T09:00:00",advanced.getJSONObject("data").getString("due_at"));
        String encrypted=SecureStore.encrypt("local-test-secret");
        assertFalse(encrypted.contains("local-test-secret"));assertEquals("local-test-secret",SecureStore.decrypt(encrypted));
        JSONObject schedule=call("autosync.config_set","{\"enabled\":true,\"mode\":\"daily\",\"times\":[\"09:00\",\"18:00\"]}");
        assertEquals(15,schedule.getInt("minimum_interval_minutes"));assertTrue(schedule.getJSONObject("status").getLong("next_at")>0);
        call("autosync.config_set","{\"enabled\":false}");
        assertEquals("platform_unsupported",call("app.autostart_get","{}").getString("reason_key"));
        com.chaquo.python.Python.getInstance().getModule("builtins").callAttr("exec", """
            import tempfile
            from pathlib import Path
            from dulwich.repo import Repo
            from qingdan.core.gitrepo import GitRepo
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                remote = root / 'remote.git'
                remote.mkdir()
                Repo.init_bare(str(remote)).close()
                a = GitRepo(root / 'a')
                a.clone(str(remote))
                a.commit_and_push({'items/task/smoke.json': b'{"title":"first"}'})
                b = GitRepo(root / 'b')
                b.clone(str(remote))
                assert b.read_tree()['items/task/smoke.json'] == b'{"title":"first"}'
                b.commit_and_push({'items/task/smoke.json': b'{"title":"updated","deleted":true}'})
                head = a.fetch()
                assert b'"deleted":true' in a.read_tree(head)['items/task/smoke.json']
            """, com.chaquo.python.Python.getInstance().getModule("builtins").callAttr("dict"));
        try(ActivityScenario<MainActivity> scenario=ActivityScenario.launch(MainActivity.class)){
            // A navigation can discard evaluateJavascript callbacks from the blank page.
            AtomicReference<Boolean> loaded=new AtomicReference<>(false);
            for(int i=0;i<120&&!loaded.get();i++){
                scenario.onActivity(a->loaded.set(a.getWebView().getProgress()==100&&a.getWebView().getUrl()!=null&&a.getWebView().getUrl().endsWith("/index.html")));
                if(!loaded.get())Thread.sleep(500);
            }
            assertTrue("Offline document did not finish loading",loaded.get());
            boolean ready=false;
            for(int i=0;i<60;i++){
                String state=js(scenario,"Boolean(document.querySelector('[data-view=inbox]'))");
                if("true".equals(state)){ready=true;break;}Thread.sleep(500);
            }
            assertTrue("Offline frontend did not boot",ready);
            js(scenario,"document.querySelector('[data-view=inbox]').click();true");
            boolean visible=false;
            for(int i=0;i<30;i++){if("true".equals(js(scenario,"document.body.innerText.includes('Android smoke task')"))){visible=true;break;}Thread.sleep(300);}
            assertTrue("Android bridge did not render saved task: "+js(scenario,"document.body.innerText.slice(-2500)"),visible);
            assertEquals("true",js(scenario,"document.documentElement.dataset.platform==='android'"));
            AtomicReference<String> systemBars=new AtomicReference<>();
            scenario.onActivity(a->{
                android.view.View decor=a.getWindow().getDecorView();
                androidx.core.view.WindowInsetsCompat insets=androidx.core.view.ViewCompat.getRootWindowInsets(decor);
                if(insets==null){systemBars.set("Missing root insets");return;}
                androidx.core.graphics.Insets safe=insets.getInsets(androidx.core.view.WindowInsetsCompat.Type.systemBars()|androidx.core.view.WindowInsetsCompat.Type.displayCutout());
                int[] position=new int[2],origin=new int[2];
                a.getWebView().getLocationOnScreen(position);decor.getLocationOnScreen(origin);
                int top=position[1]-origin[1],bottom=top+a.getWebView().getHeight();
                systemBars.set(top>=safe.top&&bottom<=decor.getHeight()-safe.bottom?"safe":"top="+top+", bottom="+bottom+", status="+safe.top+", navigation="+safe.bottom+", window="+decor.getHeight());
            });
            assertEquals("Web content overlaps system bars: "+systemBars.get(),"safe",systemBars.get());
            android.graphics.Bitmap screenshot=InstrumentationRegistry.getInstrumentation().getUiAutomation().takeScreenshot();
            if(screenshot!=null)try(java.io.FileOutputStream output=new java.io.FileOutputStream(new java.io.File(context().getExternalFilesDir(null),"android-ui.png"))){screenshot.compress(android.graphics.Bitmap.CompressFormat.PNG,100,output);}
            // Pixel 6 has a fractional CSS viewport; innerWidth is rounded to an integer.
            String metrics=js(scenario,"JSON.stringify({main:document.querySelector('.main').getBoundingClientRect().toJSON(),viewport:visualViewport.width,scroll:document.documentElement.scrollWidth})");
            assertEquals("Layout overflow: "+metrics,"true",js(scenario,"document.querySelector('.main').getBoundingClientRect().right<=visualViewport.width+1&&document.documentElement.scrollWidth<=Math.ceil(visualViewport.width)+1"));
        }
    }
    private String js(ActivityScenario<MainActivity> scenario,String script) throws Exception {
        AtomicReference<String> value=new AtomicReference<>();CountDownLatch done=new CountDownLatch(1);
        scenario.onActivity(a->a.getWebView().evaluateJavascript(script,result->{value.set(result);done.countDown();}));
        assertTrue("JS callback timed out",done.await(30,TimeUnit.SECONDS));return value.get();
    }
}
