package com.ziyueguyi.qingdan;
import android.content.Context;
import androidx.work.*;
import java.util.concurrent.TimeUnit;
public class SyncWorker extends Worker {
    public SyncWorker(Context c,WorkerParameters p){super(c,p);}
    public Result doWork(){
        try {RuntimeBridge.invoke(getApplicationContext(),"autosync.tick","{}");RuntimeBridge.invoke(getApplicationContext(),"alert.tick","{}");return Result.success();}
        catch(Exception error){return Result.retry();}
    }
    public static void install(Context context){
        Constraints network=new Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build();
        PeriodicWorkRequest work=new PeriodicWorkRequest.Builder(SyncWorker.class,15,TimeUnit.MINUTES).setConstraints(network).build();
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("qingdan-sync",ExistingPeriodicWorkPolicy.KEEP,work);
    }
}
