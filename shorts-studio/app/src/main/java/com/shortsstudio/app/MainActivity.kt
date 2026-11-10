package com.shortsstudio.app

import android.content.Intent
import android.content.ClipData
import android.content.ClipboardManager
import android.content.Context
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.foundation.text.KeyboardOptions
import androidx.compose.material.icons.Icons
import androidx.compose.material.icons.outlined.*
import androidx.compose.material3.*
import androidx.compose.runtime.*
import androidx.compose.runtime.saveable.rememberSaveable
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Brush
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.text.input.KeyboardType
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.net.HttpURLConnection
import java.net.URL
import java.time.ZoneId

private val Ink = Color(0xFF0B0C14)
private val Panel = Color(0xFF171926)
private val Panel2 = Color(0xFF202236)
private val Violet = Color(0xFF9377FA)
private val Dim = Color(0xFFA8A8BC)
private val Teal = Color(0xFF55D6B1)

private val voiceGroups = linkedMapOf(
    "a" to ("English · US" to "af_alloy af_aoede af_bella af_heart af_jessica af_kore af_nicole af_nova af_river af_sarah af_sky am_adam am_echo am_eric am_fenrir am_liam am_michael am_onyx am_puck am_santa"),
    "b" to ("English · UK" to "bf_alice bf_emma bf_isabella bf_lily bm_daniel bm_fable bm_george bm_lewis"),
    "e" to ("Spanish" to "ef_dora em_alex em_santa"),
    "f" to ("French" to "ff_siwis"),
    "h" to ("Hindi" to "hf_alpha hf_beta hm_omega hm_psi"),
    "i" to ("Italian" to "if_sara im_nicola"),
    "j" to ("Japanese" to "jf_alpha jf_gongitsune jf_nezumi jf_tebukuro jm_kumo"),
    "p" to ("Portuguese · Brazil" to "pf_dora pm_alex pm_santa"),
    "z" to ("Mandarin" to "zf_xiaobei zf_xiaoni zf_xiaoxiao zf_xiaoyi zm_yunjian zm_yunxi zm_yunxia zm_yunyang")
)
private fun voices(lang: String) = voiceGroups[lang]?.second?.split(" ") ?: emptyList()
private val languageNames = voiceGroups.map { it.key to it.value.first }

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor = android.graphics.Color.rgb(11, 12, 20)
        window.navigationBarColor = android.graphics.Color.rgb(11, 12, 20)
        setContent {
            MaterialTheme(
                colorScheme = darkColorScheme(
                    primary = Violet,
                    secondary = Teal,
                    background = Ink,
                    surface = Panel,
                    onBackground = Color.White,
                    onSurface = Color.White
                )
            ) { ShortsStudio() }
        }
    }
}

private fun allowsRunnerConnection(target: URL): Boolean {
    if (target.protocol == "https") return true
    if (target.protocol != "http") return false
    val host = target.host
    if (host == "localhost") return true
    val parts = host.split(".")
    if (parts.size != 4) return false
    val octets = parts.map { it.toIntOrNull() ?: return false }
    if (octets.any { it !in 0..255 }) return false
    return octets[0] == 127 || octets[0] == 10 ||
        (octets[0] == 192 && octets[1] == 168) ||
        (octets[0] == 172 && octets[1] in 16..31)
}

private object Api {
    fun json(base: String, token: String, route: String, method: String = "GET", body: JSONObject? = null): JSONObject {
        require(base.isNotBlank()) { "Set up your runner address in Settings first." }
        val target = URL(base.trimEnd('/') + route)
        require(allowsRunnerConnection(target)) {
            "Connect with HTTPS, or a private local-network IP such as 192.168.x.x."
        }
        val conn = target.openConnection() as HttpURLConnection
        conn.connectTimeout = 12000
        conn.readTimeout = 20000
        conn.requestMethod = method
        conn.setRequestProperty("Authorization", "Bearer $token")
        conn.setRequestProperty("Accept", "application/json")
        if (body != null) {
            conn.doOutput = true
            conn.setRequestProperty("Content-Type", "application/json; charset=utf-8")
            conn.outputStream.use { it.write(body.toString().toByteArray(Charsets.UTF_8)) }
        }
        val ok = conn.responseCode in 200..299
        val raw = (if (ok) conn.inputStream else conn.errorStream)?.bufferedReader()?.use { it.readText() } ?: ""
        conn.disconnect()
        if (!ok) throw IllegalStateException("Runner: " + raw.take(220))
        return JSONObject(raw)
    }

    fun preview(context: android.content.Context, base: String, token: String, id: Int): Uri {
        val target = URL(base.trimEnd('/') + "/videos/$id")
        require(allowsRunnerConnection(target)) {
            "Use HTTPS or a trusted private LAN for video previews."
        }
        val conn = target.openConnection() as HttpURLConnection
        conn.connectTimeout = 15000
        conn.readTimeout = 120000
        conn.setRequestProperty("Authorization", "Bearer $token")
        if (conn.responseCode != 200) {
            conn.disconnect()
            throw IllegalStateException("Video is not available on the runner.")
        }
        val folder = File(context.cacheDir, "videos").apply { mkdirs() }
        val path = File(folder, "short-$id.mp4")
        conn.inputStream.use { input -> path.outputStream().use { input.copyTo(it) } }
        conn.disconnect()
        return FileProvider.getUriForFile(context, context.packageName + ".files", path)
    }
}

@Composable
private fun ShortsStudio() {
    val ctx = LocalContext.current
    val store = remember { ctx.getSharedPreferences("studio", 0) }
    var runner by rememberSaveable { mutableStateOf(store.getString("runner", "") ?: "") }
    var token by rememberSaveable { mutableStateOf(SecretStore.load(ctx)) }
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var topic by rememberSaveable { mutableStateOf("") }
    var lang by rememberSaveable { mutableStateOf("a") }
    var voice by rememberSaveable { mutableStateOf("random") }
    var duration by rememberSaveable { mutableFloatStateOf(30f) }
    var style by rememberSaveable { mutableStateOf("Neon kinetic") }
    var repeatDaily by rememberSaveable { mutableStateOf(false) }
    var time by rememberSaveable { mutableStateOf("18:00") }
    var upload by rememberSaveable { mutableStateOf(false) }
    var privacy by rememberSaveable { mutableStateOf("private") }
    var privacyMenu by remember { mutableStateOf(false) }
    var voiceDialog by remember { mutableStateOf(false) }
    var menuLang by remember { mutableStateOf(false) }
    var running by remember { mutableStateOf(false) }
    var health by remember { mutableStateOf("Not connected") }
    var claudeLoginStatus by remember { mutableStateOf("Not checked") }
    var checkingClaude by remember { mutableStateOf(false) }
    var jobs by remember { mutableStateOf(JSONArray()) }
    var insights by remember { mutableStateOf(JSONObject()) }
    var historyJobId by remember { mutableIntStateOf(-1) }
    var historyRuns by remember { mutableStateOf(JSONArray()) }
    val snack = remember { SnackbarHostState() }
    val scope = rememberCoroutineScope()

    fun showError(t: Throwable) {
        scope.launch { snack.showSnackbar(t.message ?: "Something went wrong") }
    }
    fun fetchJobs() {
        scope.launch {
            try {
                val result = withContext(Dispatchers.IO) { Api.json(runner, token, "/jobs") }
                jobs = result.getJSONArray("jobs")
                try { insights = withContext(Dispatchers.IO) { Api.json(runner, token, "/analytics") } }
                catch (_: Exception) { /* Supports older runner versions. */ }
            } catch (e: Exception) { showError(e) }
        }
    }
    LaunchedEffect(tab) { if (tab == 1 && runner.isNotEmpty()) fetchJobs() }

    Scaffold(
        containerColor = Ink,
        snackbarHost = { SnackbarHost(snack) },
        bottomBar = {
            NavigationBar(containerColor = Panel) {
                val labels = listOf("Create", "Queue", "Settings")
                val icons = listOf(Icons.Outlined.AutoAwesome, Icons.Outlined.VideoLibrary, Icons.Outlined.Tune)
                labels.forEachIndexed { index, label ->
                    NavigationBarItem(
                        selected = tab == index,
                        onClick = { tab = index },
                        icon = { Icon(icons[index], contentDescription = label) },
                        label = { Text(label) },
                        colors = NavigationBarItemDefaults.colors(selectedIconColor = Violet, selectedTextColor = Color.White, indicatorColor = Panel2, unselectedIconColor = Dim, unselectedTextColor = Dim)
                    )
                }
            }
        }
    ) { padding ->
        Column(Modifier.fillMaxSize().padding(padding).padding(horizontal = 19.dp)) {
            Spacer(Modifier.height(22.dp))
            Row(verticalAlignment = Alignment.CenterVertically) {
                Box(Modifier.size(42.dp).background(Brush.linearGradient(listOf(Violet, Color(0xFF4A8DC8))), RoundedCornerShape(14.dp)), contentAlignment = Alignment.Center) {
                    Icon(Icons.Outlined.MovieCreation, null, tint = Color.White)
                }
                Spacer(Modifier.width(12.dp))
                Column {
                    Text("SHORTS STUDIO", fontWeight = FontWeight.Bold, fontSize = 17.sp, letterSpacing = 1.1.sp)
                    Text("Motion on autopilot", fontSize = 12.sp, color = Dim)
                }
                Spacer(Modifier.weight(1f))
                Box(Modifier.background(Panel2, RoundedCornerShape(100.dp)).padding(horizontal = 12.dp, vertical = 7.dp)) {
                    Text("BETA", color = Teal, fontSize = 10.sp, fontWeight = FontWeight.Bold)
                }
            }
            Spacer(Modifier.height(23.dp))
            when (tab) {
                0 -> Column(Modifier.verticalScroll(rememberScrollState())) {
                    Hero()
                    Spacer(Modifier.height(22.dp))
                    Heading("1  •  The idea")
                    Spacer(Modifier.height(10.dp))
                    OutlinedTextField(
                        value = topic, onValueChange = { topic = it },
                        modifier = Modifier.fillMaxWidth(),
                        minLines = 3, maxLines = 5,
                        label = { Text("What should your Short be about?") },
                        placeholder = { Text("e.g. Why black holes bend time, explained in 40 seconds") },
                        shape = RoundedCornerShape(17.dp)
                    )
                    Spacer(Modifier.height(18.dp))
                    Heading("2  •  Motion style")
                    Spacer(Modifier.height(9.dp))
                    Row(horizontalArrangement = Arrangement.spacedBy(7.dp)) {
                        listOf("Neon kinetic", "Minimal", "Cyber").forEach { s ->
                            FilterChip(selected = style == s, onClick = { style = s }, label = { Text(s, fontSize = 11.sp) })
                        }
                    }
                    Spacer(Modifier.height(14.dp))
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Heading("3  •  Duration")
                        Spacer(Modifier.weight(1f))
                        Text("${duration.toInt()} sec", color = Violet, fontWeight = FontWeight.SemiBold)
                    }
                    Slider(value = duration, onValueChange = { duration = it }, valueRange = 15f..60f, steps = 8)
                    Heading("4  •  Narrator")
                    Spacer(Modifier.height(10.dp))
                    Box {
                        OutlinedButton(
                            onClick = { menuLang = true },
                            modifier = Modifier.fillMaxWidth(),
                            shape = RoundedCornerShape(16.dp)
                        ) {
                            Icon(Icons.Outlined.Translate, null)
                            Spacer(Modifier.width(9.dp))
                            Text(languageNames.firstOrNull { it.first == lang }?.second ?: "Language")
                            Spacer(Modifier.weight(1f))
                            Icon(Icons.Outlined.KeyboardArrowDown, null)
                        }
                        DropdownMenu(expanded = menuLang, onDismissRequest = { menuLang = false }) {
                            languageNames.forEach { (code, label) ->
                                DropdownMenuItem(text = { Text(label) }, onClick = {
                                    lang = code
                                    voice = "random"
                                    menuLang = false
                                })
                            }
                        }
                    }
                    Spacer(Modifier.height(7.dp))
                    OutlinedButton(onClick = { voiceDialog = true }, modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(16.dp)) {
                        Icon(Icons.Outlined.GraphicEq, null)
                        Spacer(Modifier.width(9.dp))
                        Text(if (voice == "random") "Random voice (from ${voices(lang).size} voices)" else voice)
                        Spacer(Modifier.weight(1f))
                        Icon(Icons.Outlined.KeyboardArrowDown, null)
                    }
                    Spacer(Modifier.height(16.dp))
                    Heading("5  •  Automation")
                    SettingSwitch("Repeat every day", "Run the pipeline on your runner each day", repeatDaily) { repeatDaily = it }
                    if (repeatDaily) {
                        OutlinedTextField(
                            value = time, onValueChange = { time = it },
                            modifier = Modifier.fillMaxWidth(),
                            label = { Text("Daily time · HH:mm") },
                            singleLine = true, keyboardOptions = KeyboardOptions(keyboardType = KeyboardType.Number),
                            shape = RoundedCornerShape(15.dp)
                        )
                        Text("Timezone: ${ZoneId.systemDefault().id}", fontSize = 12.sp, color = Dim, modifier = Modifier.padding(top = 5.dp))
                    }
                    SettingSwitch("Auto-upload to YouTube", "Private by default. Review before sharing publicly.", upload) { upload = it }
                    if (upload) {
                        Box {
                            OutlinedButton(onClick = { privacyMenu = true }, modifier = Modifier.fillMaxWidth()) {
                                Text("YouTube visibility: " + privacy.replaceFirstChar { it.uppercase() })
                                Spacer(Modifier.weight(1f))
                                Icon(Icons.Outlined.KeyboardArrowDown, null)
                            }
                            DropdownMenu(expanded = privacyMenu, onDismissRequest = { privacyMenu = false }) {
                                listOf("private", "unlisted", "public").forEach { option ->
                                    DropdownMenuItem(text = { Text(option.replaceFirstChar { it.uppercase() }) }, onClick = {
                                        privacy = option
                                        privacyMenu = false
                                    })
                                }
                            }
                        }
                        if (privacy == "public") {
                            Text("Public uploads go live without manual review. Verify your content and rights first.", color = Color(0xFFF0C68A), fontSize = 12.sp)
                        }
                    }
                    Spacer(Modifier.height(20.dp))
                    Button(
                        onClick = {
                            if (topic.trim().length < 8) {
                                scope.launch { snack.showSnackbar("Describe the topic with at least 8 characters.") }
                            } else if (repeatDaily && !Regex("^([01]\\d|2[0-3]):[0-5]\\d$").matches(time)) {
                                scope.launch { snack.showSnackbar("Use a 24-hour time, e.g. 18:00") }
                            } else {
                                running = true
                                scope.launch {
                                    try {
                                        val data = JSONObject()
                                            .put("topic", topic.trim())
                                            .put("lang", lang)
                                            .put("voice", voice)
                                            .put("style", style)
                                            .put("duration_sec", duration.toInt())
                                            .put("auto_upload", upload)
                                            .put("privacy", privacy)
                                            .put("repeat_daily", repeatDaily)
                                            .put("time", time)
                                            .put("timezone", ZoneId.systemDefault().id)
                                        withContext(Dispatchers.IO) { Api.json(runner, token, "/jobs", "POST", data) }
                                        snack.showSnackbar("Job added to your runner.")
                                        topic = ""
                                        tab = 1
                                        fetchJobs()
                                    } catch (e: Exception) { showError(e) }
                                    finally { running = false }
                                }
                            }
                        },
                        enabled = !running,
                        modifier = Modifier.fillMaxWidth().height(58.dp),
                        shape = RoundedCornerShape(17.dp),
                        colors = ButtonDefaults.buttonColors(containerColor = Violet)
                    ) {
                        if (running) CircularProgressIndicator(modifier = Modifier.size(20.dp), strokeWidth = 2.dp, color = Color.White)
                        else Icon(Icons.Outlined.AutoAwesome, null)
                        Spacer(Modifier.width(9.dp))
                        Text(if (repeatDaily) "Create automation" else "Generate my Short", fontWeight = FontWeight.Bold, color = Color(0xFF0B0C14))
                    }
                    Spacer(Modifier.height(22.dp))
                }
                1 -> Column(Modifier.fillMaxSize()) {
                    Row(verticalAlignment = Alignment.CenterVertically) {
                        Heading("Your video pipeline")
                        Spacer(Modifier.weight(1f))
                        IconButton(onClick = { fetchJobs() }) { Icon(Icons.Outlined.Refresh, "Refresh", tint = Violet) }
                    }
                    Spacer(Modifier.height(10.dp))
                    if (insights.length() > 0) {
                        val stats = insights.optJSONObject("videos") ?: JSONObject()
                        Surface(color = Panel, shape = RoundedCornerShape(16.dp), modifier = Modifier.fillMaxWidth()) {
                            Row(Modifier.padding(14.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                                Column {
                                    Text("Published", fontSize = 11.sp, color = Dim)
                                    Text(stats.optInt("posted").toString(), fontSize = 22.sp, fontWeight = FontWeight.Bold, color = Teal)
                                }
                                Column {
                                    Text("Ready", fontSize = 11.sp, color = Dim)
                                    Text(stats.optInt("ready").toString(), fontSize = 22.sp, fontWeight = FontWeight.Bold)
                                }
                                Column {
                                    Text("Needs review", fontSize = 11.sp, color = Dim)
                                    Text(stats.optInt("needs_review").toString(), fontSize = 22.sp, fontWeight = FontWeight.Bold, color = Color(0xFFF0C68A))
                                }
                            }
                        }
                        Spacer(Modifier.height(12.dp))
                    }
                    if (jobs.length() == 0) {
                        Box(Modifier.fillMaxWidth().padding(top = 60.dp), contentAlignment = Alignment.Center) {
                            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                                Icon(Icons.Outlined.VideoLibrary, null, tint = Violet, modifier = Modifier.size(55.dp))
                                Spacer(Modifier.height(16.dp))
                                Text("No videos in queue yet")
                                Text("Create your first Short to get started", color = Dim, fontSize = 12.sp)
                            }
                        }
                    } else {
                        val list = remember(jobs) { (0 until jobs.length()).map { jobs.getJSONObject(it) } }
                        LazyColumn(verticalArrangement = Arrangement.spacedBy(10.dp), contentPadding = PaddingValues(bottom = 22.dp)) {
                            items(list) { item ->
                                val id = item.optInt("id")
                                val status = item.optString("status")
                                Surface(color = Panel, shape = RoundedCornerShape(19.dp)) {
                                    Column(Modifier.fillMaxWidth().padding(16.dp)) {
                                        Row(verticalAlignment = Alignment.CenterVertically) {
                                            Icon(Icons.Outlined.PlayCircle, null, tint = Violet)
                                            Spacer(Modifier.width(9.dp))
                                            Text(item.optString("topic"), maxLines = 2, overflow = TextOverflow.Ellipsis, fontWeight = FontWeight.SemiBold, modifier = Modifier.weight(1f))
                                        }
                                        Spacer(Modifier.height(9.dp))
                                        Row(verticalAlignment = Alignment.CenterVertically) {
                                            Text(status.uppercase(), fontWeight = FontWeight.Bold, fontSize = 11.sp, color = if (status == "posted" || status == "ready") Teal else Violet)
                                            Spacer(Modifier.width(12.dp))
                                            Text(item.optString("voice"), color = Dim, fontSize = 11.sp)
                                        }
                                        if (item.optString("error").isNotBlank()) {
                                            Text(item.optString("error"), color = Color(0xFFF3A5A9), fontSize = 12.sp)
                                        }
                                        if (item.optString("next_run").isNotBlank()) {
                                            Text("Next: ${item.optString("next_run")}", color = Dim, fontSize = 11.sp)
                                        }
                                        if (item.optBoolean("has_video")) {
                                            TextButton(onClick = {
                                                scope.launch {
                                                    try {
                                                        val uri = withContext(Dispatchers.IO) { Api.preview(ctx, runner, token, id) }
                                                        ctx.startActivity(Intent(Intent.ACTION_VIEW).apply {
                                                            setDataAndType(uri, "video/mp4")
                                                            addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
                                                        })
                                                    } catch (e: Exception) { showError(e) }
                                                }
                                            }) { Icon(Icons.Outlined.Visibility, null); Spacer(Modifier.width(5.dp)); Text("Preview video") }
                                        }
                                        if (item.optString("next_run").isNotBlank()) {
                                            val paused = item.optBoolean("paused")
                                            TextButton(onClick = {
                                                scope.launch {
                                                    try {
                                                        withContext(Dispatchers.IO) {
                                                            Api.json(runner, token, "/jobs/$id/" + (if (paused) "resume" else "pause"), "POST")
                                                        }
                                                        fetchJobs()
                                                    } catch (e: Exception) { showError(e) }
                                                }
                                            }) {
                                                Icon(if (paused) Icons.Outlined.PlayArrow else Icons.Outlined.Pause, null)
                                                Spacer(Modifier.width(5.dp))
                                                Text(if (paused) "Resume daily" else "Pause daily")
                                            }
                                        }
                                        TextButton(onClick = {
                                            scope.launch {
                                                try {
                                                    val result = withContext(Dispatchers.IO) {
                                                        Api.json(runner, token, "/jobs/$id/runs")
                                                    }
                                                    historyRuns = result.getJSONArray("runs")
                                                    historyJobId = id
                                                } catch (e: Exception) { showError(e) }
                                            }
                                        }) {
                                            Icon(Icons.Outlined.History, null)
                                            Spacer(Modifier.width(5.dp))
                                            Text("Video history")
                                        }
                                        val link = item.optString("youtube_url")
                                        if (link.startsWith("https://")) {
                                            TextButton(onClick = { openLink(ctx, link) }) {
                                                Icon(Icons.Outlined.OpenInNew, null)
                                                Spacer(Modifier.width(5.dp)); Text("View on YouTube")
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                else -> Column(Modifier.verticalScroll(rememberScrollState())) {
                    Heading("Your creative runner")
                    Spacer(Modifier.height(10.dp))
                    Text("Your phone is the remote control. Claude Code, Kokoro TTS and FFmpeg run on your own computer or server.", color = Dim, fontSize = 13.sp)
                    Spacer(Modifier.height(16.dp))
                    OutlinedTextField(value = runner, onValueChange = { runner = it }, modifier = Modifier.fillMaxWidth(), label = { Text("Runner URL") }, placeholder = { Text("https://studio.example.com") }, singleLine = true)
                    Spacer(Modifier.height(9.dp))
                    OutlinedTextField(value = token, onValueChange = { token = it }, modifier = Modifier.fillMaxWidth(),
                        label = { Text("Runner access token") }, singleLine = true,
                        visualTransformation = PasswordVisualTransformation())
                    Spacer(Modifier.height(11.dp))
                    Button(onClick = {
                        store.edit().putString("runner", runner.trimEnd('/')).apply()
                        SecretStore.save(ctx, token)
                        running = true
                        scope.launch {
                            try {
                                val result = withContext(Dispatchers.IO) { Api.json(runner, token, "/health") }
                                health = "Connected · Claude CLI " + if (result.optBoolean("claude_installed")) "installed" else "not installed"
                                snack.showSnackbar("Runner settings saved.")
                            } catch (e: Exception) { health = "Offline"; showError(e) }
                            finally { running = false }
                        }
                    }, shape = RoundedCornerShape(14.dp), enabled = !running) {
                        Text("Save & test connection")
                    }
                    Spacer(Modifier.height(9.dp))
                    Text(health, color = if (health.startsWith("Connected")) Teal else Dim, fontSize = 12.sp)
                    Spacer(Modifier.height(25.dp))
                    Heading("Claude sign-in")
                    Spacer(Modifier.height(8.dp))
                    InfoBlock("Sign in through the official Claude mobile app or website. ShortsStudio never asks for your Claude password, authorization code, or session token.")
                    Spacer(Modifier.height(9.dp))
                    Button(onClick = { openLink(ctx, "https://claude.ai/code") },
                        modifier = Modifier.fillMaxWidth(), shape = RoundedCornerShape(14.dp)) {
                        Icon(Icons.Outlined.Login, null)
                        Spacer(Modifier.width(8.dp))
                        Text("Sign in with Claude (official app)")
                    }
                    Spacer(Modifier.height(12.dp))
                    Heading("Claude on your video runner")
                    Spacer(Modifier.height(7.dp))
                    Text("Claude mobile sign-in is separate from the Claude Code CLI running your ShortsStudio videos. The runner must sign in once using Anthropic's official terminal login.", color = Dim, fontSize = 12.sp)
                    Spacer(Modifier.height(10.dp))
                    Surface(color = Panel, shape = RoundedCornerShape(12.dp), modifier = Modifier.fillMaxWidth()) {
                        Row(Modifier.padding(13.dp), verticalAlignment = Alignment.CenterVertically) {
                            Icon(Icons.Outlined.SmartToy, null, tint = Violet)
                            Spacer(Modifier.width(10.dp))
                            Text(claudeLoginStatus, modifier = Modifier.weight(1f), fontSize = 12.sp)
                        }
                    }
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(
                        modifier = Modifier.fillMaxWidth(),
                        enabled = !checkingClaude && runner.isNotBlank(),
                        onClick = {
                            checkingClaude = true
                            scope.launch {
                                try {
                                    val status = withContext(Dispatchers.IO) {
                                        Api.json(runner, token, "/claude/status")
                                    }
                                    claudeLoginStatus = when {
                                        !status.optBoolean("installed") -> "Claude Code is not installed on runner"
                                        status.optBoolean("authenticated") -> "Runner authenticated (" + status.optString("method", "Claude") + ")"
                                        else -> "Runner not signed in — use the official CLI login"
                                    }
                                } catch (e: Exception) {
                                    claudeLoginStatus = "Cannot check runner login"
                                    showError(e)
                                } finally {
                                    checkingClaude = false
                                }
                            }
                        }
                    ) {
                        if (checkingClaude) CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp)
                        else Icon(Icons.Outlined.Refresh, null)
                        Spacer(Modifier.width(8.dp))
                        Text("Check runner Claude sign-in")
                    }
                    Spacer(Modifier.height(8.dp))
                    OutlinedButton(onClick = {
                        val clipboard = ctx.getSystemService(Context.CLIPBOARD_SERVICE) as ClipboardManager
                        clipboard.setPrimaryClip(ClipData.newPlainText("Claude Code login", "claude auth login"))
                        scope.launch { snack.showSnackbar("Login command copied. Run it in the runner's own terminal.") }
                    }, modifier = Modifier.fillMaxWidth()) {
                        Icon(Icons.Outlined.ContentCopy, null)
                        Spacer(Modifier.width(8.dp))
                        Text("Copy runner sign-in command")
                    }
                    Spacer(Modifier.height(7.dp))
                    Text("Run the copied command on the runner using its own terminal or an SSH client on your phone. Complete Anthropic's browser approval there; if asked, paste the code into the terminal, not ShortsStudio.", color = Dim, fontSize = 12.sp)
                    TextButton(onClick = { openLink(ctx, "https://code.claude.com/docs/en/authentication") }) {
                        Text("Official Claude Code authentication help")
                        Spacer(Modifier.width(4.dp))
                        Icon(Icons.Outlined.OpenInNew, null, modifier = Modifier.size(15.dp))
                    }
                    Spacer(Modifier.height(22.dp))
                    Heading("YouTube channel")
                    Spacer(Modifier.height(8.dp))
                    Text("YouTube authorization happens through Google OAuth. Configure your Google OAuth client on the runner first.", color = Dim, fontSize = 13.sp)
                    Spacer(Modifier.height(9.dp))
                    OutlinedButton(onClick = {
                        scope.launch {
                            try {
                                val url = withContext(Dispatchers.IO) { Api.json(runner, token, "/auth/youtube/start").getString("url") }
                                openLink(ctx, url)
                            } catch (e: Exception) { showError(e) }
                        }
                    }, modifier = Modifier.fillMaxWidth()) {
                        Icon(Icons.Outlined.Link, null)
                        Spacer(Modifier.width(7.dp))
                        Text("Connect YouTube securely")
                    }
                    Spacer(Modifier.height(24.dp))
                    Text("HTTPS is required for remote connections. Private LAN HTTP is supported for older local runners, but the token is not encrypted in transit on HTTP. Daily jobs need the runner online.", color = Dim, fontSize = 12.sp)
                }
            }
        }
    }

    if (historyJobId >= 0) {
        AlertDialog(
            onDismissRequest = { historyJobId = -1 },
            title = { Text("Recent videos") },
            text = {
                LazyColumn(Modifier.heightIn(max = 350.dp)) {
                    items((0 until historyRuns.length()).map { historyRuns.getJSONObject(it) }) { run ->
                        Column(Modifier.fillMaxWidth().padding(vertical = 9.dp)) {
                            Text(run.optString("scheduled"), fontSize = 12.sp, color = Dim)
                            Text(run.optString("status").uppercase(), fontWeight = FontWeight.Bold)
                            if (run.optString("youtube_url").startsWith("https://")) {
                                TextButton(onClick = { openLink(ctx, run.optString("youtube_url")) }) {
                                    Text("Open on YouTube")
                                }
                            }
                            if (run.optString("error").isNotBlank()) Text(run.optString("error"), color = Color(0xFFF0A1A1), fontSize = 12.sp)
                        }
                    }
                }
            },
            confirmButton = { TextButton(onClick = { historyJobId = -1 }) { Text("Close") } }
        )
    }

    if (voiceDialog) {
        var search by remember { mutableStateOf("") }
        AlertDialog(
            onDismissRequest = { voiceDialog = false },
            title = { Text("Choose narrator") },
            text = {
                Column {
                    Text("${voices(lang).size} natural voices for ${voiceGroups[lang]?.first}", color = Dim, fontSize = 12.sp)
                    OutlinedTextField(value = search, onValueChange = { search = it }, label = { Text("Search voices") }, singleLine = true, modifier = Modifier.fillMaxWidth())
                    Spacer(Modifier.height(8.dp))
                    LazyColumn(Modifier.heightIn(max = 350.dp)) {
                        item {
                            TextButton(onClick = { voice = "random"; voiceDialog = false }) { Text("🎲  Random voice") }
                        }
                        items(voices(lang).filter { it.contains(search, true) }) { item ->
                            TextButton(onClick = { voice = item; voiceDialog = false }, modifier = Modifier.fillMaxWidth()) {
                                Text(item.replace('_', ' ').replaceFirstChar { it.uppercase() }, modifier = Modifier.weight(1f))
                                if (voice == item) Icon(Icons.Outlined.Check, null)
                            }
                        }
                    }
                }
            },
            confirmButton = { TextButton(onClick = { voiceDialog = false }) { Text("Done") } }
        )
    }
}

@Composable private fun Heading(s: String) { Text(s, color = Color.White, fontSize = 16.sp, fontWeight = FontWeight.Bold) }

@Composable private fun SettingSwitch(title: String, subtitle: String, checked: Boolean, onChange: (Boolean) -> Unit) {
    Row(Modifier.fillMaxWidth().padding(vertical = 6.dp), verticalAlignment = Alignment.CenterVertically) {
        Column(Modifier.weight(1f)) {
            Text(title, fontSize = 14.sp, fontWeight = FontWeight.Medium)
            Text(subtitle, fontSize = 11.sp, color = Dim)
        }
        Switch(checked = checked, onCheckedChange = onChange)
    }
}

@Composable private fun InfoBlock(s: String) {
    Surface(color = Panel, shape = RoundedCornerShape(16.dp)) {
        Text(s, color = Dim, fontSize = 13.sp, modifier = Modifier.padding(16.dp))
    }
}

@Composable private fun Hero() {
    Box(
        Modifier.fillMaxWidth().height(148.dp)
            .background(Brush.linearGradient(listOf(Color(0xFF352D65), Color(0xFF173449), Color(0xFF1A1831))), RoundedCornerShape(22.dp))
            .padding(18.dp)
    ) {
        Column {
            Icon(Icons.Outlined.AutoAwesome, null, tint = Teal)
            Spacer(Modifier.height(14.dp))
            Text("Ideas in. Shorts out.", fontWeight = FontWeight.ExtraBold, fontSize = 24.sp)
            Spacer(Modifier.height(3.dp))
            Text("Script → motion → voice → publish", fontSize = 12.sp, color = Color(0xFFE2DBF7))
        }
        Icon(Icons.Outlined.SmartDisplay, null, tint = Color.White.copy(alpha = .18f), modifier = Modifier.align(Alignment.CenterEnd).size(96.dp))
    }
}

private fun openLink(context: android.content.Context, link: String) {
    context.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(link)))
}
