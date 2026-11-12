package com.ziyueguyi.qingdan;
import android.content.Context;
import com.chaquo.python.Python;
public final class RuntimeBridge {
    private static boolean ready=false;
    private static synchronized void init(Context context) {
        if(!ready) {Python.getInstance().getModule("qingdan_android").callAttr("initialize",context.getFilesDir().getAbsolutePath()+"/data");ready=true;}
    }
    public static String invoke(Context context,String name,String params) {
        init(context);
        return Python.getInstance().getModule("qingdan_android").callAttr("dispatch",name,params).toString();
    }
}
