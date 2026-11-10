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
import org.json.JSONObject
import java.net.URI
import java.net.URL
import java.security.KeyStore
import javax.crypto.Cipher
import javax.crypto.KeyGenerator
import javax.crypto.SecretKey
import javax.crypto.spec.GCMParameterSpec

object CloudEndpoint {
    /** Parse the owner's private one-time Modal setup link; never share the fragment. */
    fun parseSetupLink(value: String): Pair<String, String> {
        val uri = URI(value.trim())
        val endpoint = URI(uri.scheme, null, uri.host, uri.port, uri.path, null, null).toString().trimEnd('/')
        require(isAllowed(endpoint) && uri.rawQuery == null) { "Use your private HTTPS Modal setup link" }
        val fragment = uri.rawFragment.orEmpty().split('&')
        val key = fragment.firstOrNull { it.startsWith("key=") }?.substringAfter('=') ?: ""
        require(key.matches(Regex("[A-Za-z0-9_-]{32,128}"))) { "The setup link is missing its private connection key" }
        return endpoint to key
    }

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

    fun pairFromLink(ctx: Context, privateSetupLink: String) {
        val (endpoint, key) = CloudEndpoint.parseSetupLink(privateSetupLink)
        save(ctx, endpoint, key)
    }

    /** Deployed Modal service wakes automatically when it receives this request. */
    suspend fun status(ctx: Context): JSONObject = withContext(Dispatchers.IO) {
        val (endpoint, token) = load(ctx) ?: error("Connect your private cloud once")
        CloudHttp.getJson(URL("$endpoint/status"), token, 100_000)
    }
    fun openLogin(ctx: Context) {
        require(load(ctx) != null) { "Pair this APK using the Connect APK button on your private Modal setup page first." }
        ctx.startActivity(Intent(ctx, CloudLoginActivity::class.java))
    }
    suspend fun generate(ctx: Context, topic: String): String =
        request(ctx,"/generate",JSONObject().put("topic",topic.take(500)),210_000).getString("text")

    suspend fun direct(ctx: Context, brief: String, seconds: Int): MotionPlan =
        MotionPlan.parse(request(ctx,"/direct",JSONObject().put("brief",brief.take(4000)).put("seconds",seconds),550_000).toString())

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
        if(uri?.scheme != "shortsstudio" || uri.host != "connect" || !CloudEndpoint.isAllowed(endpoint) ||
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
                if(url.scheme == "shortsstudio" && url.host == "connect") return true
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
