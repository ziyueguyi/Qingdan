package com.ziyueguyi.qingdan;
import com.chaquo.python.android.PyApplication;
public class QingdanApplication extends PyApplication {
    private static QingdanApplication instance;
    @Override public void onCreate(){super.onCreate();instance=this;}
    public static QingdanApplication get(){return instance;}
}
