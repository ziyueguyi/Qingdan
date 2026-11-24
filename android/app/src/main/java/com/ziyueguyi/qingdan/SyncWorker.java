package com.ziyueguyi.qingdan;
import android.content.Context;
import androidx.work.*;
import java.util.concurrent.TimeUnit;
public class SyncWorker extends Worker {
    private static boolean installed;
    public SyncWorker(Context c,WorkerParameters p){super(c,p);}
    public Result doWork(){
        try {return WorkerOutcome.fromResponse(RuntimeBridge.invoke(getApplicationContext(),"autosync.tick","{}"));}
        catch(Exception error){return Result.retry();}
    }
    /**
     * Registers both periodic workers once per process. UPDATE re-registers an existing WorkSpec
     * (keeping its period and enqueue time) so that a periodic worker left ENQUEUED without an OS
     * job by WorkManager 2.10.1's rescheduling race is scheduled again the next time the app opens.
     */
    public static synchronized void install(Context context){
        if(installed)return;
        installed=true;
        Constraints network=new Constraints.Builder().setRequiredNetworkType(NetworkType.CONNECTED).build();
        PeriodicWorkRequest work=new PeriodicWorkRequest.Builder(SyncWorker.class,15,TimeUnit.MINUTES).setConstraints(network).setBackoffCriteria(BackoffPolicy.EXPONENTIAL,30,TimeUnit.SECONDS).build();
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("qingdan-sync",ExistingPeriodicWorkPolicy.UPDATE,work);
        PeriodicWorkRequest reminders=new PeriodicWorkRequest.Builder(AlertWorker.class,15,TimeUnit.MINUTES).build();
        WorkManager.getInstance(context).enqueueUniquePeriodicWork("qingdan-alerts",ExistingPeriodicWorkPolicy.UPDATE,reminders);
    }
}
