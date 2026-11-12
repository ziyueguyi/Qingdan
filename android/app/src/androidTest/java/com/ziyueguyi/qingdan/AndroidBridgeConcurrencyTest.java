package com.ziyueguyi.qingdan;

import android.content.Context;
import androidx.test.platform.app.InstrumentationRegistry;
import androidx.test.ext.junit.runners.AndroidJUnit4;
import com.chaquo.python.Python;
import com.chaquo.python.PyObject;
import org.json.JSONObject;
import org.junit.Test;
import org.junit.runner.RunWith;
import java.util.concurrent.*;
import static org.junit.Assert.*;

@RunWith(AndroidJUnit4.class)
public class AndroidBridgeConcurrencyTest {
    @Test public void slowOperationDoesNotBlockOtherNativeRequests() throws Exception {
        Context context=InstrumentationRegistry.getInstrumentation().getTargetContext();
        RuntimeBridge.invoke(context,"app.info","{}");
        PyObject builtins=Python.getInstance().getModule("builtins");
        PyObject scope=builtins.callAttr("dict");
        builtins.callAttr("exec", """
            import threading
            from qingdan.core.registry import feature, REGISTRY
            entered, release = threading.Event(), threading.Event()
            @feature(name='test.wait', group='test', http=('GET','/api/v1/test/wait'), manages_lock=True)
            def wait_operation():
                entered.set()
                if not release.wait(15):
                    raise RuntimeError('test release timed out')
                return {'finished': True}
            """,scope);
        ExecutorService executor=Executors.newFixedThreadPool(2);
        Future<String> slow=executor.submit(()->RuntimeBridge.invoke(context,"test.wait","{}"));
        try {
            assertEquals("True",builtins.callAttr("eval","entered.wait(10)",scope).toString());
            Future<String> fast=executor.submit(()->RuntimeBridge.invoke(context,"task.list","{}"));
            JSONObject reply=new JSONObject(fast.get(3,TimeUnit.SECONDS));
            assertTrue("Native requests were serialized behind a slow operation",reply.getBoolean("ok"));
        } finally {
            builtins.callAttr("exec","release.set()",scope);
            try {assertTrue(new JSONObject(slow.get(10,TimeUnit.SECONDS)).getBoolean("ok"));}
            finally {
                executor.shutdownNow();
                builtins.callAttr("exec","REGISTRY.pop('test.wait', None)",scope);
            }
        }
    }
}
