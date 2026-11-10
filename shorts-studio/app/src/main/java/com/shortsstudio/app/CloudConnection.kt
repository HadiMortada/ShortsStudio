package com.shortsstudio.app

import android.app.Activity
import android.app.AlertDialog
import android.content.Context
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.security.keystore.KeyGenParameterSpec
import android.security.keystore.KeyProperties
import android.util.Base64
import android.webkit.WebResourceRequest
import android.webkit.WebView
import android.webkit.WebViewClient
import android.widget.TextView
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import kotlinx.coroutines.delay
import kotlinx.coroutines.withTimeout
import java.io.File
import org.json.JSONObject
import java.net.URI
import java.net.URL
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

object CloudEndpoint {
    fun isAllowed(value: String): Boolean = try {
        val u = URI(value)
        val host = u.host.orEmpty().lowercase()
        u.scheme == "https" && host.endsWith(".modal.run") && host != ".modal.run" &&
            u.userInfo == null && u.port == -1 && u.rawQuery == null && u.rawFragment == null &&
            (u.rawPath.isNullOrEmpty() || u.rawPath == "/")
    } catch (_: Exception) { false }
}

object CloudConnection {
    private const val ALIAS = "shorts-studio-cloud-v1"
    private fun prefs(ctx: Context) = ctx.getSharedPreferences("cloud", Context.MODE_PRIVATE)
    private fun key(): SecretKey {
        val store = KeyStore.getInstance("AndroidKeyStore").apply { load(null) }
        (store.getKey(ALIAS, null) as? SecretKey)?.let { return it }
        return KeyGenerator.getInstance(KeyProperties.KEY_ALGORITHM_AES, "AndroidKeyStore").apply {
            init(KeyGenParameterSpec.Builder(ALIAS, KeyProperties.PURPOSE_ENCRYPT or KeyProperties.PURPOSE_DECRYPT)
                .setBlockModes(KeyProperties.BLOCK_MODE_GCM).setEncryptionPaddings(KeyProperties.ENCRYPTION_PADDING_NONE).build())
        }.generateKey()
    }
    fun save(ctx: Context, endpoint: String, token: String) {
        require(CloudEndpoint.isAllowed(endpoint)) { "Expected your HTTPS Modal endpoint" }
        require(token.matches(Regex("[A-Za-z0-9_-]{32,128}"))) { "Invalid connection link" }
        val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply { init(Cipher.ENCRYPT_MODE, key()) }
        val raw = JSONObject().put("endpoint", endpoint.trimEnd('/')).put("key", token).toString().toByteArray()
        val encrypted = cipher.doFinal(raw)
        check(prefs(ctx).edit().putString("iv", Base64.encodeToString(cipher.iv, Base64.NO_WRAP))
            .putString("data", Base64.encodeToString(encrypted, Base64.NO_WRAP)).commit()) { "Could not save connection" }
    }
    fun load(ctx: Context): Pair<String, String>? {
        val p = prefs(ctx)
        val data = p.getString("data", null) ?: return null
        val iv = p.getString("iv", null) ?: return null
        return try {
            val cipher = Cipher.getInstance("AES/GCM/NoPadding").apply {
                init(Cipher.DECRYPT_MODE, key(), GCMParameterSpec(128, Base64.decode(iv, Base64.NO_WRAP)))
            }
            val j = JSONObject(String(cipher.doFinal(Base64.decode(data, Base64.NO_WRAP))))
            val endpoint = j.getString("endpoint")
            if (!CloudEndpoint.isAllowed(endpoint)) null else endpoint to j.getString("key")
        } catch (_: Exception) { null }
    }
    fun clear(ctx: Context) { prefs(ctx).edit().clear().apply() }
    fun openLogin(ctx: Context) {
        require(load(ctx) != null) { "Pair this APK using the Connect APK button on your private Modal setup page first." }
        ctx.startActivity(Intent(ctx, CloudLoginActivity::class.java))
    }
    suspend fun generate(ctx: Context, topic: String): String =
        request(ctx,"/generate",JSONObject().put("topic",topic.take(500)),210_000).getString("text")

    suspend fun direct(ctx: Context, brief: String, seconds: Int): MotionPlan =
        MotionPlan.parse(request(ctx,"/direct",JSONObject().put("brief",brief.take(4000)).put("seconds",seconds),550_000).toString())

    data class NeuralVoice(val id:String,val label:String,val language:String)
    val neuralVoices = listOf(
        NeuralVoice("auto","Automatic narrator",""),
        NeuralVoice("en-US-AndrewMultilingualNeural","Andrew · warm conversational male","English"),
        NeuralVoice("en-US-AvaMultilingualNeural","Ava · expressive female","English"),
        NeuralVoice("en-US-GuyNeural","Guy · warm male","English"),
        NeuralVoice("en-US-JennyNeural","Jenny · clear female","English"),
        NeuralVoice("en-GB-RyanNeural","Ryan · male","British English"),
        NeuralVoice("en-GB-SoniaNeural","Sonia · female","British English"),
        NeuralVoice("ar-LB-RamiNeural","Rami · male","Arabic · Lebanese"),
        NeuralVoice("ar-LB-LaylaNeural","Layla · female","Arabic · Lebanese"),
        NeuralVoice("ar-SA-HamedNeural","Hamed · male","Arabic"),
        NeuralVoice("ar-SA-ZariyahNeural","Zariyah · female","Arabic")
    )

    suspend fun produce(ctx:Context,brief:String,seconds:Int,voice:String,
                        onProgress:(String)->Unit={}):File = withTimeout(25*60*1000L) {
        withContext(Dispatchers.IO) {
            val (endpoint,token)=load(ctx) ?: error("Connect your Modal server in Accounts first")
            val selection=voice.takeIf { id -> neuralVoices.any { it.id==id } } ?: "auto"
            val body=JSONObject().put("brief",brief.take(4000)).put("seconds",seconds).put("voice",selection)
            val pending=ctx.getSharedPreferences("cloud-production",Context.MODE_PRIVATE)
            val fingerprint=endpoint+body.toString()
            var id=if(pending.getString("request","")==fingerprint) pending.getString("id","").orEmpty() else ""
            if(id.isBlank()) {
                val job=CloudHttp.postJson(URL("$endpoint/productions"),token,body.toString(),30_000)
                id=job.getString("id")
                check(id.matches(Regex("[a-f0-9]{32}"))) { "The server returned an invalid video ID" }
                pending.edit().putString("id",id).putString("request",fingerprint).apply()
            }
            var failures=0
            while(true) {
                val job=try {
                    CloudHttp.getJson(URL("$endpoint/productions/$id"),token)
                } catch(e:java.io.IOException) {
                    if(++failures>=5)throw e
                    delay(3000);continue
                }
                failures=0
                withContext(Dispatchers.Main) { onProgress(job.optString("message","Creating your film…")) }
                when(job.getString("state")) {
                    "ready" -> {
                        withContext(Dispatchers.Main) { onProgress("Downloading your 1080p motion graphic…") }
                        val directory=File(ctx.filesDir,"videos").apply { mkdirs() }
                        val result=File(directory,"film-$id.mp4")
                        if(!result.exists())CloudHttp.downloadVideo(URL("$endpoint/productions/$id/video"),token,result)
                        pending.edit().clear().apply()
                        return@withContext result
                    }
                    "failed" -> {
                        pending.edit().clear().apply()
                        error(job.optString("message","Video creation failed. Try again."))
                    }
                }
                delay(2500)
            }
            @Suppress("UNREACHABLE_CODE")
            error("Video creation ended")
        }
    }

    private suspend fun request(ctx: Context, path: String, body: JSONObject, readTimeout: Int): JSONObject = withContext(Dispatchers.IO) {
        val (endpoint, token) = load(ctx) ?: error("Connect your Modal server in Accounts first")
        CloudHttp.postJson(URL("$endpoint$path"),token,body.toString(),readTimeout)
    }
}

class CloudPairActivity : Activity() {
    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        val uri = intent.data
        val endpoint = uri?.getQueryParameter("endpoint").orEmpty()
        val token = uri?.getQueryParameter("key").orEmpty()
        if(uri?.scheme != "shortsstudiohd" || uri.host != "connect" || !CloudEndpoint.isAllowed(endpoint) ||
            !token.matches(Regex("[A-Za-z0-9_-]{32,128}"))) {
            AlertDialog.Builder(this).setMessage("This connection link is invalid.").setPositiveButton("Close") { _,_ -> finish() }.show()
            return
        }
        AlertDialog.Builder(this).setTitle("Connect your private Modal server?")
            .setMessage("$endpoint\n\nConfirm this is the server you deployed. Claude sign-in is a separate step. Your existing connection is replaced only if you confirm.")
            .setNegativeButton("Cancel") { _,_ -> finish() }.setOnCancelListener { finish() }
            .setPositiveButton("Connect") { _,_ ->
                try {
                    CloudConnection.save(this, endpoint, token)
                    startActivity(Intent(this, CloudLoginActivity::class.java))
                    finish()
                } catch(e: Exception) {
                    AlertDialog.Builder(this).setMessage(e.message ?: "Unable to save connection").setPositiveButton("Close") { _,_ -> finish() }.show()
                }
            }.show()
    }
}

class CloudLoginActivity : Activity() {
    private var browser: WebView? = null
    override fun onCreate(state: Bundle?) {
        super.onCreate(state)
        val config = CloudConnection.load(this)
        if(config == null) { setContentView(TextView(this).apply { text="Pair your Modal server first." }); return }
        val (endpoint, token) = config
        val view = WebView(this)
        browser = view
        view.settings.apply {
            javaScriptEnabled = true
            domStorageEnabled = true
            allowFileAccess = false
            allowContentAccess = false
            mixedContentMode = android.webkit.WebSettings.MIXED_CONTENT_NEVER_ALLOW
            safeBrowsingEnabled = true
        }
        view.webViewClient = object : WebViewClient() {
            override fun shouldOverrideUrlLoading(view: WebView, request: WebResourceRequest): Boolean {
                val url = request.url
                if(url.scheme == "https" && url.host == Uri.parse(endpoint).host) return false
                if(url.scheme == "shortsstudiohd" && url.host == "connect") return true
                if(url.scheme == "https" && listOf("claude.ai","claude.com").any { url.host==it || url.host.orEmpty().endsWith(".$it") }) {
                    startActivity(Intent(Intent.ACTION_VIEW,url))
                }
                return true
            }
        }
        // An ordinary link opens in this WebView first, then is routed to the browser above.
        view.settings.setSupportMultipleWindows(false)
        setContentView(view)
        view.loadUrl("$endpoint/#key=${Uri.encode(token)}")
    }
    override fun onDestroy() {
        browser?.apply { stopLoading(); clearHistory(); destroy() }
        browser = null
        super.onDestroy()
    }
}

