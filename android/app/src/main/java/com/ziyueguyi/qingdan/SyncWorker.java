package com.ziyueguyi.qingdan;
import android.content.Context;
import androidx.work.*;
import java.util.concurrent.TimeUnit;
public class SyncWorker extends Worker {
    public SyncWorker(Context c,WorkerParameters p){super(c,p);}
    public Result doWork(){
        try {return WorkerOutcome.fromResponse(RuntimeBridge.invoke(getApplicationContext(),"autosync.tick","{}"));}
        catch(Exception error){return Result.retry();}
    }
    public static void install(Context context){
        Constraints network=new Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build();
        PeriodicWorkRequest work=new PeriodicWorkRequest.Builder(SyncWorker.class,15,TimeUnit.MINUTES).setConstraints(network).setBackoffCriteria(BackoffPolicy.EXPONENTIAL,30,TimeUnit.SECONDS).build();
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("qingdan-sync",ExistingPeriodicWorkPolicy.KEEP,work);
        PeriodicWorkRequest reminders=new PeriodicWorkRequest.Builder(AlertWorker.class,15,TimeUnit.MINUTES).build();
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("qingdan-alerts",ExistingPeriodicWorkPolicy.KEEP,reminders);
    }
}
