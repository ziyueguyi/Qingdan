package com.ziyueguyi.qingdan;
import android.app.*;
import android.content.*;
import android.content.pm.PackageManager;
import android.net.Uri;
import android.os.*;
import android.webkit.*;
import android.widget.*;
import androidx.webkit.WebViewAssetLoader;
import androidx.core.content.FileProvider;
import org.json.JSONObject;
import java.io.File;
import java.util.concurrent.ExecutorService;
import java.util.concurrent.Executors;

public class MainActivity extends Activity {
    private WebView web;
    private final ExecutorService executor=Executors.newSingleThreadExecutor();
    private ValueCallback<Uri[]> fileCallback;
    private static final String HOME="https://appassets.androidplatform.net/assets/web/index.html";
    @Override public void onCreate(Bundle state){
        super.onCreate(state);
        web=new WebView(this);setContentView(web);
        web.setOnApplyWindowInsetsListener((v,insets)->{v.setPadding(insets.getSystemWindowInsetLeft(),insets.getSystemWindowInsetTop(),insets.getSystemWindowInsetRight(),insets.getSystemWindowInsetBottom());return insets;});
        WebSettings settings=web.getSettings();settings.setJavaScriptEnabled(true);settings.setDomStorageEnabled(false);settings.setAllowFileAccess(false);settings.setAllowContentAccess(true);settings.setMixedContentMode(WebSettings.MIXED_CONTENT_NEVER_ALLOW);
        WebViewAssetLoader loader=new WebViewAssetLoader.Builder().addPathHandler("/assets/",new WebViewAssetLoader.AssetsPathHandler(this)).build();
        web.setWebViewClient(new WebViewClient(){
            @Override public WebResourceResponse shouldInterceptRequest(WebView view,WebResourceRequest request){
                WebResourceResponse result=loader.shouldInterceptRequest(request.getUrl());
                if(result!=null)return result;
                return new WebResourceResponse("text/plain","UTF-8",403,"Blocked",java.util.Collections.emptyMap(),new java.io.ByteArrayInputStream(new byte[0]));
            }
            @Override public boolean shouldOverrideUrlLoading(WebView view,WebResourceRequest request){
                Uri url=request.getUrl();
                if("appassets.androidplatform.net".equals(url.getHost())&&"https".equals(url.getScheme()))return false;
                if(request.hasGesture()&&("https".equals(url.getScheme())||"http".equals(url.getScheme())))try{startActivity(new Intent(Intent.ACTION_VIEW,url));}catch(Exception ignored){}
                return true;
            }
        });
        web.setWebChromeClient(new WebChromeClient(){
            @Override public boolean onJsConfirm(WebView v,String url,String message,JsResult result){new AlertDialog.Builder(MainActivity.this).setMessage(message).setPositiveButton(android.R.string.ok,(d,w)->result.confirm()).setNegativeButton(android.R.string.cancel,(d,w)->result.cancel()).setOnCancelListener(d->result.cancel()).show();return true;}
            @Override public boolean onJsAlert(WebView v,String url,String message,JsResult result){new AlertDialog.Builder(MainActivity.this).setMessage(message).setPositiveButton(android.R.string.ok,(d,w)->result.confirm()).setOnCancelListener(d->result.cancel()).show();return true;}
            @Override public boolean onJsPrompt(WebView v,String url,String message,String initial,JsPromptResult result){EditText input=new EditText(MainActivity.this);input.setText(initial);new AlertDialog.Builder(MainActivity.this).setMessage(message).setView(input).setPositiveButton(android.R.string.ok,(d,w)->result.confirm(input.getText().toString())).setNegativeButton(android.R.string.cancel,(d,w)->result.cancel()).setOnCancelListener(d->result.cancel()).show();return true;}
            @Override public boolean onShowFileChooser(WebView v,ValueCallback<Uri[]> callback,FileChooserParams params){if(fileCallback!=null)fileCallback.onReceiveValue(null);fileCallback=callback;Intent intent=new Intent(Intent.ACTION_OPEN_DOCUMENT).addCategory(Intent.CATEGORY_OPENABLE).setType("application/json");startActivityForResult(intent,7);return true;}
        });
        web.addJavascriptInterface(new NativeApi(),"QingdanAndroid");
        web.loadUrl(HOME);SyncWorker.install(this);
        if(Build.VERSION.SDK_INT>=33)getOnBackInvokedDispatcher().registerOnBackInvokedCallback(android.window.OnBackInvokedDispatcher.PRIORITY_DEFAULT,this::handleBack);
        if(Build.VERSION.SDK_INT>=33&&checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS)!=PackageManager.PERMISSION_GRANTED)requestPermissions(new String[]{android.Manifest.permission.POST_NOTIFICATIONS},8);
    }
    public WebView getWebView(){return web;}
    public class NativeApi {
        @JavascriptInterface public void invoke(String id,String feature,String params){
            if(id.length()>100||feature.length()>100||params.length()>8_000_000)return;
            executor.execute(()->{
                String response;
                try {response=RuntimeBridge.invoke(getApplicationContext(),feature,params);}
                catch(Exception error){response="{\"ok\":false,\"error\":{\"code\":\"ANDROID_STARTUP\",\"message\":\"Unable to initialize local data\"}}";android.util.Log.e("Qingdan","Runtime startup failed",error);}
                String reply=response;
                runOnUiThread(()->{if(!isDestroyed())web.evaluateJavascript("window.qingdanNativeResult("+JSONObject.quote(id)+","+JSONObject.quote(reply)+")",null);});
            });
        }
        @JavascriptInterface public void shareFile(String path){
            try {
                File file=new File(path).getCanonicalFile();File allowed=new File(getFilesDir(),"data/exports").getCanonicalFile();
                if(!file.getPath().startsWith(allowed.getPath()+File.separator)||!file.isFile())return;
                Uri uri=FileProvider.getUriForFile(MainActivity.this,getPackageName()+".files",file);
                runOnUiThread(()->{Intent send=new Intent(Intent.ACTION_SEND).setType("application/octet-stream").putExtra(Intent.EXTRA_STREAM,uri).addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);startActivity(Intent.createChooser(send,"导出清单"));});
            }catch(Exception ignored){}
        }
    }
    @Override protected void onActivityResult(int request,int result,Intent data){super.onActivityResult(request,result,data);if(request==7&&fileCallback!=null){fileCallback.onReceiveValue(result==RESULT_OK&&data!=null?new Uri[]{data.getData()}:null);fileCallback=null;}}
    private void handleBack(){web.evaluateJavascript("(()=>{const d=document.querySelector('dialog[open]');if(d){d.close();return true;}const m=document.getElementById('mask');if(m&&!m.hidden){const b=m.querySelector('[data-act=close-modal]');if(b)b.click();else m.hidden=true;return true;}const a=document.getElementById('app');if(a.classList.contains('phone-detail')){a.classList.remove('phone-detail');return true;}if(a.classList.contains('phone-nav')){a.classList.remove('phone-nav');return true;}return false;})()",handled->{if(!"true".equals(handled))finish();});}
    @Override public void onBackPressed(){handleBack();}
    @Override protected void onResume(){super.onResume();if(!executor.isShutdown())executor.execute(()->RuntimeBridge.invoke(getApplicationContext(),"host.foreground","{\"active\":true}"));}
    @Override protected void onPause(){super.onPause();if(!executor.isShutdown())executor.execute(()->RuntimeBridge.invoke(getApplicationContext(),"host.foreground","{\"active\":false}"));}
    @Override protected void onDestroy(){if(fileCallback!=null)fileCallback.onReceiveValue(null);executor.shutdown();web.removeJavascriptInterface("QingdanAndroid");web.destroy();super.onDestroy();}
}
