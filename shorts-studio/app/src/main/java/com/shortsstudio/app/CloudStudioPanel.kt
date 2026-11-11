package com.shortsstudio.app

import android.content.Intent
import android.net.Uri
import android.webkit.ValueCallback
import android.webkit.WebChromeClient
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.runtime.*
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.viewinterop.AndroidView
import org.json.JSONTokener

/** Paired Studio only. No JavaScript interface or credential-bearing external URLs. */
@Composable
fun CloudStudioPanel(endpoint:String, token:String, modifier:Modifier=Modifier, onSave:(String)->Unit) {
    val ctx=LocalContext.current
    val save by rememberUpdatedState(onSave)
    var chooser by remember { mutableStateOf<ValueCallback<Array<Uri>>?>(null) }
    val launcher=rememberLauncherForActivityResult(ActivityResultContracts.StartActivityForResult()) { result ->
        chooser?.onReceiveValue(WebChromeClient.FileChooserParams.parseResult(result.resultCode,result.data))
        chooser=null
    }
    val view=remember(endpoint,token) {
        WebView(ctx).apply {
            settings.javaScriptEnabled=true
            settings.domStorageEnabled=true
            settings.allowFileAccess=false
            settings.allowContentAccess=true // System picker URIs for user-selected references.
            settings.mixedContentMode=android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW
            settings.safeBrowsingEnabled=true
            webChromeClient=object:WebChromeClient() {
                override fun onShowFileChooser(webView:WebView, callback:ValueCallback<Array<Uri>>,
                    params:FileChooserParams):Boolean {
                    chooser?.onReceiveValue(null)
                    chooser=callback
                    try { launcher.launch(params.createIntent()) }
                    catch(_:Exception) { chooser?.onReceiveValue(null);chooser=null }
                    return true
                }
            }
            webViewClient=object:WebViewClient() {
                override fun shouldOverrideUrlLoading(webView:WebView,request:WebResourceRequest):Boolean {
                    val uri=request.url
                    if(uri.scheme=="https" && uri.host==Uri.parse(endpoint).host && uri.port==-1) return false
                    if(uri.scheme=="https" && request.isForMainFrame) {
                        try { ctx.startActivity(Intent(Intent.ACTION_VIEW,uri)) } catch(_:Exception) { }
                    }
                    return true
                }
            }
            // Blob downloads must use the native authenticated MP4 endpoint,
            // so finished films are atomically saved and indexed in Library.
            setDownloadListener { _,_,_,mime,_ ->
                if(mime.substringBefore(';')=="video/mp4" && Uri.parse(url.orEmpty()).host==Uri.parse(endpoint).host) {
                    evaluateJavascript("typeof currentId==='string'?currentId:''") { raw ->
                        val id=runCatching { JSONTokener(raw).nextValue() as? String }.getOrNull().orEmpty()
                        if(id.matches(Regex("[a-f0-9]{32}"))) save(id)
                    }
                }
            }
            loadUrl("$endpoint/studio#key=${Uri.encode(token)}")
        }
    }
    DisposableEffect(view) {
        onDispose { chooser?.onReceiveValue(null);chooser=null;view.stopLoading();view.destroy() }
    }
    AndroidView(factory={view},modifier=modifier)
}
