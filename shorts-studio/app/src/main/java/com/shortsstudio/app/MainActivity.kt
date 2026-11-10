package com.shortsstudio.app

import android.Manifest
import android.content.Intent
import android.net.Uri
import android.os.Build
import android.os.Bundle
import android.speech.tts.TextToSpeech
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.*
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.shape.RoundedCornerShape
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
import androidx.compose.ui.text.input.PasswordVisualTransformation
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import org.json.JSONArray
import java.io.File
import java.time.LocalTime

private val Dark = Color(0xFF0B0C14)
private val Pane = Color(0xFF171925)
private val Soft = Color(0xFF24253B)
private val Purple = Color(0xFF997EFD)
private val Muted = Color(0xFFABACBC)
private val Mint = Color(0xFF59DEB5)

class MainActivity : ComponentActivity() {
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        window.statusBarColor=android.graphics.Color.rgb(11,12,20)
        window.navigationBarColor=android.graphics.Color.rgb(11,12,20)
        setContent {
            MaterialTheme(colorScheme=darkColorScheme(primary=Purple,background=Dark,
                surface=Pane,secondary=Mint,onSurface=Color.White)) {
                StudioScreen()
            }
        }
    }
}

@Composable
private fun StudioScreen() {
    val ctx = LocalContext.current
    val prefs = remember { ctx.getSharedPreferences("mobile",0) }
    val scope= rememberCoroutineScope()
    val snackbar= remember { SnackbarHostState() }
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var topic by rememberSaveable { mutableStateOf(prefs.getString("topic","") ?: "") }
    var duration by rememberSaveable { mutableFloatStateOf(30f) }
    var voiceId by rememberSaveable { mutableStateOf("random") }
    var voices by remember { mutableStateOf(emptyList<android.speech.tts.Voice>()) }
    var voicePicker by remember { mutableStateOf(false) }
    var voiceFilter by remember { mutableStateOf("") }
    var generating by remember { mutableStateOf(false) }
    var generateMessage by remember { mutableStateOf("") }
    var creativeDirection by remember { mutableStateOf("") }
    var cloudStatus by remember { mutableStateOf(if (CloudConnection.load(ctx) == null) "Not connected" else "Paired • Checking connection") }
    var probingCloud by remember { mutableStateOf(false) }
    var privateSetupLink by remember { mutableStateOf("") }
    var upcoming by remember { mutableStateOf(prefs.getString("schedule","").orEmpty()) }
    var clock by rememberSaveable { mutableStateOf("18:00") }
    var autoDaily by rememberSaveable { mutableStateOf(false) }
    var videos by remember { mutableStateOf(MobileJobs.list(ctx)) }
    val notifyPermission = rememberLauncherForActivityResult(ActivityResultContracts.RequestPermission()) { }

    DisposableEffect(Unit) {
        val engine = TextToSpeech(ctx) { /* voice list is updated asynchronously below */ }
        // TTS engines can initialize late, so poll briefly from a coroutine.
        val task=scope.launch {
            repeat(12) {
                try {
                    val list = engine.voices?.filter { !it.isNetworkConnectionRequired }?.sortedBy { it.name }
                    if(!list.isNullOrEmpty()) { voices=list; return@launch }
                } catch(_: Exception) {}
                kotlinx.coroutines.delay(500)
            }
        }
        onDispose { task.cancel(); engine.shutdown() }
    }

    fun notify(s:String) { scope.launch { snackbar.showSnackbar(s) } }

    fun checkClaudeCloud() {
        if (CloudConnection.load(ctx) == null) {
            cloudStatus = "Not connected"
            return
        }
        probingCloud = true
        cloudStatus = "Connecting to your cloud… (cold start may take a moment)"
        scope.launch {
            try {
                val status = CloudConnection.status(ctx)
                cloudStatus = when {
                    status.optBoolean("busy") -> "Cloud online • Claude is working"
                    status.optBoolean("loggedIn") -> "Connected • Claude signed in"
                    else -> "Cloud online • Claude needs sign-in"
                }
            } catch (e: Exception) {
                cloudStatus = "Cloud unavailable • Check whether shorts-studio-cloud is deployed"
            } finally {
                probingCloud = false
            }
        }
    }

    LaunchedEffect(tab) {
        if (tab == 2 && CloudConnection.load(ctx) != null) {
            // A deployed Modal endpoint wakes itself when this status request arrives.
            val result = try { CloudConnection.status(ctx) } catch (_: Exception) { null }
            cloudStatus = when {
                result == null -> "Cloud unavailable • Check its deployment"
                result.optBoolean("busy") -> "Cloud online • Claude is working"
                result.optBoolean("loggedIn") -> "Connected • Claude signed in"
                else -> "Cloud online • Claude needs sign-in"
            }
        }
    }

    Scaffold(
        containerColor=Dark,
        snackbarHost={ SnackbarHost(snackbar) },
        bottomBar={
            NavigationBar(containerColor=Pane) {
                listOf(Triple("Create", Icons.Outlined.AutoAwesome,0),
                    Triple("Library", Icons.Outlined.VideoLibrary,1),
                    Triple("Accounts", Icons.Outlined.AccountCircle,2)).forEach { (label,icon,idx) ->
                    NavigationBarItem(selected=tab==idx,onClick={ tab=idx
                        if(idx==1) videos=MobileJobs.list(ctx)
                    },icon={ Icon(icon,label) },label={Text(label)},
                        colors=NavigationBarItemDefaults.colors(
                            selectedIconColor=Purple,selectedTextColor=Color.White,indicatorColor=Soft,
                            unselectedIconColor=Muted,unselectedTextColor=Muted))
                }
            }
        }
    ) { pad ->
        Column(Modifier.fillMaxSize().padding(pad).padding(horizontal=18.dp)) {
            Spacer(Modifier.height(21.dp))
            Row(verticalAlignment=Alignment.CenterVertically) {
                Box(Modifier.size(47.dp).background(
                    Brush.linearGradient(listOf(Purple,Color(0xFF5D93D8))),RoundedCornerShape(15.dp)),
                    contentAlignment=Alignment.Center) {
                    Icon(Icons.Outlined.MovieCreation,null,tint=Color.White,modifier=Modifier.size(26.dp))
                }
                Spacer(Modifier.width(12.dp))
                Column {
                    Text("SHORTS STUDIO",fontSize=18.sp,fontWeight=FontWeight.Black,letterSpacing=1.sp)
                    Text("Claude in your cloud · video on your phone",fontSize=12.sp,color=Muted)
                }
                Spacer(Modifier.weight(1f))
                Icon(Icons.Outlined.PhoneAndroid,null,tint=Mint,modifier=Modifier.size(24.dp))
            }
            Spacer(Modifier.height(23.dp))

            when(tab) {
                0 -> Column(Modifier.verticalScroll(rememberScrollState())) {
                    Banner()
                    Spacer(Modifier.height(20.dp))
                    Section("Your video idea")
                    Spacer(Modifier.height(8.dp))
                    OutlinedTextField(topic,{ topic=it; prefs.edit().putString("topic",it).apply() },
                        modifier=Modifier.fillMaxWidth(),label={Text("Describe your motion graphic")},
                        placeholder={Text("e.g. Introduce a new AI model with dramatic typography, diagrams and a confident reveal.")},
                        minLines=2,maxLines=4,shape=RoundedCornerShape(15.dp))
                    Spacer(Modifier.height(12.dp))
                    Section("Claude directs the whole video")
                    Text("Opus 5.5 chooses the story, typography, colors, layouts and animation keyframes. Describe what you want, then create it in one step.",
                        color=Muted,fontSize=12.sp)
                    Spacer(Modifier.height(14.dp))
                    Row(verticalAlignment=Alignment.CenterVertically) {
                        Section("Video length")
                        Spacer(Modifier.weight(1f))
                        Text("${duration.toInt()} seconds",color=Purple,fontWeight=FontWeight.SemiBold)
                    }
                    Slider(duration,{duration=it},valueRange=15f..60f,steps=8)
                    Section("Narrator")
                    Spacer(Modifier.height(6.dp))
                    OutlinedButton(onClick={voicePicker=true},
                        modifier=Modifier.fillMaxWidth(),shape=RoundedCornerShape(14.dp)) {
                        Icon(Icons.Outlined.GraphicEq,null)
                        Spacer(Modifier.width(10.dp))
                        Text(if(voiceId=="random") "Auto voice for the narration language" else voiceId,
                            maxLines=1,overflow=TextOverflow.Ellipsis,modifier=Modifier.weight(1f))
                        Icon(Icons.Outlined.ExpandMore,null)
                    }
                    Text("Voices come from your Android TTS engine. Install additional language/voice packs in phone Settings.",
                        fontSize=11.sp,color=Muted)
                    Spacer(Modifier.height(17.dp))
                    Button(
                        enabled=!generating && topic.isNotBlank(),
                        onClick={
                            generating=true
                            generateMessage="Opus 5.5 is directing the story and motion…"
                            creativeDirection=""
                            if(Build.VERSION.SDK_INT>=33) notifyPermission.launch(Manifest.permission.POST_NOTIFICATIONS)
                            scope.launch {
                                try {
                                    val plan=CloudConnection.direct(ctx,topic,duration.toInt())
                                    creativeDirection=plan.direction
                                    generateMessage="Rendering Claude's custom animation and narration on your phone…"
                                    val language=java.util.Locale.forLanguageTag(plan.language).language
                                    val chosen = if(voiceId=="random") voices.firstOrNull { it.locale.language==language }?.name.orEmpty() else voiceId
                                    val result=MobileVideo.make(ctx,topic,plan.narration,emptyList(),chosen,"",kotlin.math.ceil(plan.seconds.toDouble()).toInt(),plan)
                                    MobileJobs.saveVideo(ctx,result,topic)
                                    videos=MobileJobs.list(ctx)
                                    generateMessage="Video saved on your phone"
                                    notify("Video ready! Open Library to preview or share with YouTube.")
                                    tab=1
                                } catch(e:Exception) {
                                    generateMessage="Generation failed: ${e.message}"
                                    notify(generateMessage)
                                } finally { generating=false }
                            }
                        },
                        modifier=Modifier.fillMaxWidth().height(56.dp),
                        shape=RoundedCornerShape(17.dp),
                        colors=ButtonDefaults.buttonColors(containerColor=Purple)) {
                        if(generating) CircularProgressIndicator(Modifier.size(21.dp),strokeWidth=2.dp,color=Color.White)
                        else Icon(Icons.Outlined.MovieFilter,null,tint=Dark)
                        Spacer(Modifier.width(10.dp))
                        Text(if(generating) "Claude is creating…" else "Create with Claude",
                            fontWeight=FontWeight.Bold,color=Dark)
                    }
                    if(creativeDirection.isNotBlank()) {
                        Spacer(Modifier.height(8.dp)); Text(creativeDirection,fontSize=12.sp,color=Mint)
                    }
                    if(generateMessage.isNotBlank()) {
                        Spacer(Modifier.height(6.dp)); Text(generateMessage,fontSize=12.sp,color=Muted)
                    }
                    Spacer(Modifier.height(24.dp))
                    Section("Daily automation")
                    Spacer(Modifier.height(6.dp))
                    Row(verticalAlignment=Alignment.CenterVertically) {
                        Column(Modifier.weight(1f)) {
                            Text("Create one video every day",fontSize=14.sp,fontWeight=FontWeight.Medium)
                            Text("Fresh Claude direction each day; Android may delay background work.",
                                fontSize=11.sp,color=Muted)
                        }
                        Switch(checked=autoDaily,onCheckedChange={autoDaily=it})
                    }
                    if(autoDaily) {
                        OutlinedTextField(clock,{clock=it},label={Text("Local time · HH:mm")},
                            modifier=Modifier.fillMaxWidth(),singleLine=true)
                        Spacer(Modifier.height(8.dp))
                        Button(onClick={
                            if(topic.isBlank()) notify("Enter a topic first")
                            else try {
                                MobileJobs.scheduleDirector(ctx,topic,voiceId,duration.toInt(),clock)
                                upcoming=clock
                                notify("Daily render scheduled for around $clock.")
                            } catch(e:Exception) { notify(e.message ?: "Invalid time") }
                        }) { Icon(Icons.Outlined.Schedule,null); Spacer(Modifier.width(6.dp)); Text("Save daily schedule") }
                    }
                    if(upcoming.isNotEmpty()) {
                        Spacer(Modifier.height(9.dp))
                        Text("Active daily schedule: $upcoming",color=Mint,fontSize=12.sp)
                        TextButton(onClick={MobileJobs.cancel(ctx);upcoming="";autoDaily=false}) {Text("Cancel schedule")}
                    }
                    Spacer(Modifier.height(23.dp))
                }
                1 -> Column {
                    Row(verticalAlignment=Alignment.CenterVertically) {
                        Section("Videos on this phone")
                        Spacer(Modifier.weight(1f))
                        IconButton(onClick={videos=MobileJobs.list(ctx)}) {Icon(Icons.Outlined.Refresh,"Refresh",tint=Purple)}
                    }
                    Spacer(Modifier.height(6.dp))
                    if(videos.isEmpty()) {
                        Spacer(Modifier.height(76.dp))
                        Column(Modifier.fillMaxWidth(),horizontalAlignment=Alignment.CenterHorizontally) {
                            Icon(Icons.Outlined.VideoLibrary,null,Modifier.size(60.dp),tint=Purple)
                            Spacer(Modifier.height(12.dp))
                            Text("No local videos yet")
                            Text("Create a video to see it here",color=Muted,fontSize=12.sp)
                        }
                    } else {
                        LazyColumn(verticalArrangement=Arrangement.spacedBy(11.dp)) {
                            items(videos) { (title,file) ->
                                Surface(color=Pane,shape=RoundedCornerShape(17.dp)) {
                                    Column(Modifier.fillMaxWidth().padding(14.dp)) {
                                        Row(verticalAlignment=Alignment.CenterVertically) {
                                            Icon(Icons.Outlined.PlayCircle,null,tint=Purple)
                                            Spacer(Modifier.width(9.dp))
                                            Text(title,maxLines=2,overflow=TextOverflow.Ellipsis,
                                                fontWeight=FontWeight.SemiBold,modifier=Modifier.weight(1f))
                                        }
                                        Spacer(Modifier.height(8.dp))
                                        Text("${file.length()/1024} KB · MP4 · On-device",
                                            fontSize=12.sp,color=Muted)
                                        Row {
                                            TextButton(onClick={viewVideo(ctx,file)}) {
                                                Icon(Icons.Outlined.Visibility,null); Spacer(Modifier.width(5.dp)); Text("Preview")
                                            }
                                            Spacer(Modifier.width(7.dp))
                                            TextButton(onClick={shareVideo(ctx,file,title)}) {
                                                Icon(Icons.Outlined.Share,null); Spacer(Modifier.width(5.dp)); Text("Send to YouTube")
                                            }
                                        }
                                    }
                                }
                            }
                        }
                    }
                }
                else -> Column(Modifier.verticalScroll(rememberScrollState())) {
                    Section("Connect your accounts")
                    Spacer(Modifier.height(8.dp))
                    Text("Once your personal cloud is deployed and paired, create and render videos here without opening Modal each time.",
                        fontSize=13.sp,color=Muted)
                    Spacer(Modifier.height(17.dp))
                    AccountBox("Claude Code","Your private cloud • phone-based MP4 rendering",
                        Icons.Outlined.AutoAwesome) {
                        Row(verticalAlignment=Alignment.CenterVertically) {
                            Icon(if (cloudStatus.startsWith("Connected")) Icons.Outlined.CheckCircle else Icons.Outlined.Cloud,
                                null, tint=if (cloudStatus.startsWith("Connected")) Mint else Purple,
                                modifier=Modifier.size(20.dp))
                            Spacer(Modifier.width(9.dp))
                            Text(cloudStatus,color=if (cloudStatus.startsWith("Connected")) Mint else Muted,
                                fontSize=12.sp,modifier=Modifier.weight(1f))
                        }
                        Spacer(Modifier.height(9.dp))
                        if (CloudConnection.load(ctx) != null) {
                            Button(onClick={
                                try { CloudConnection.openLogin(ctx) }
                                catch(e:Exception) { notify(e.message ?: "Cloud unavailable") }
                            },shape=RoundedCornerShape(12.dp)) {
                                Icon(Icons.Outlined.AutoAwesome,null,Modifier.size(18.dp))
                                Spacer(Modifier.width(7.dp))
                                Text("Sign in / Manage Claude")
                            }
                            Spacer(Modifier.height(8.dp))
                            OutlinedButton(onClick={ checkClaudeCloud() },
                                enabled=!probingCloud,shape=RoundedCornerShape(12.dp)) {
                                if (probingCloud) CircularProgressIndicator(Modifier.size(17.dp),strokeWidth=2.dp)
                                else Icon(Icons.Outlined.Refresh,null,Modifier.size(18.dp))
                                Spacer(Modifier.width(7.dp))
                                Text("Check / Reconnect")
                            }
                            Text("Your paired cloud starts on demand when you use ShortsStudio, as long as the Modal deployment remains active.",
                                color=Muted,fontSize=12.sp)
                        } else {
                            Text("One-time connection: deploy your private cloud, then paste its private setup link here. The app saves it encrypted.",
                                color=Muted,fontSize=12.sp)
                            Spacer(Modifier.height(8.dp))
                            OutlinedTextField(
                                value=privateSetupLink,onValueChange={privateSetupLink=it},
                                label={Text("Paste private Modal setup link")},
                                placeholder={Text("https://…modal.run/#key=…")},
                                singleLine=true,
                                visualTransformation=PasswordVisualTransformation(),
                                modifier=Modifier.fillMaxWidth()
                            )
                            Spacer(Modifier.height(8.dp))
                            Button(onClick={
                                try {
                                    CloudConnection.pairFromLink(ctx,privateSetupLink)
                                    privateSetupLink=""
                                    cloudStatus="Paired • Checking connection"
                                    checkClaudeCloud()
                                    notify("Cloud paired. You can sign in to Claude from this app.")
                                } catch(e:Exception) {
                                    notify(e.message ?: "Invalid setup link")
                                }
                            }, enabled=privateSetupLink.isNotBlank(), shape=RoundedCornerShape(12.dp)) {
                                Text("Connect my private cloud")
                            }
                            Text("Already deployed? You only need to pair once; you do not need to run Modal again for every Short.",
                                color=Muted,fontSize=12.sp)
                            OutlinedButton(onClick={openSite(ctx,"https://modal.com/playground/get_started")},
                                shape=RoundedCornerShape(12.dp)) { Text("One-time Modal deployment") }
                            OutlinedButton(onClick={openSite(ctx,"https://github.com/HadiMortada/ShortsStudio/blob/main/shorts-studio/cloud/modal_bridge.py")},
                                shape=RoundedCornerShape(12.dp)) { Text("View cloud setup code") }
                        }
                        if (CloudConnection.load(ctx) != null) {
                            TextButton(onClick={
                                CloudConnection.clear(ctx)
                                cloudStatus="Not connected"
                                notify("Phone unpaired; cloud Claude login was not removed.")
                            }) { Text("Disconnect this phone") }
                        }
                    }
                    Spacer(Modifier.height(16.dp))
                    AccountBox("YouTube","Use your existing YouTube app login",
                        Icons.Outlined.SmartDisplay) {
                        Button(onClick={
                            val launch=ctx.packageManager.getLaunchIntentForPackage("com.google.android.youtube")
                            if(launch!=null) ctx.startActivity(launch)
                            else openSite(ctx,"https://www.youtube.com/")
                        },shape=RoundedCornerShape(12.dp)) {
                            Icon(Icons.Outlined.OpenInNew,null,Modifier.size(18.dp))
                            Spacer(Modifier.width(7.dp))
                            Text("Open YouTube / Sign in")
                        }
                        Spacer(Modifier.height(7.dp))
                        Text("When you select 'Send to YouTube' from the Library, the Android share sheet opens YouTube with your selected account. Direct unattended uploads need a registered Google OAuth application and YouTube API authorization; this version does not pretend that app login grants automatic uploads.",
                            color=Muted,fontSize=12.sp)
                    }
                    Spacer(Modifier.height(16.dp))
                    AccountBox("Voices","Android Text-to-Speech installed language packs",
                        Icons.Outlined.RecordVoiceOver) {
                        Text("${voices.size} offline-capable voices currently detected",
                            color=Mint,fontSize=13.sp)
                        Spacer(Modifier.height(6.dp))
                        OutlinedButton(onClick={
                            ctx.startActivity(Intent("com.android.settings.TTS_SETTINGS").apply {
                                addFlags(Intent.FLAG_ACTIVITY_NEW_TASK)
                            })
                        }) { Text("Manage Android TTS voices") }
                    }
                    Spacer(Modifier.height(18.dp))
                    Text("Background tasks can be delayed or stopped by Android power saving. Keep enough free storage and test a short video before scheduling.",
                        fontSize=12.sp,color=Muted)
                    Spacer(Modifier.height(20.dp))
                }
            }
        }
    }

    if (voicePicker) {
        AlertDialog(onDismissRequest={voicePicker=false},title={Text("Narrator voices")},
            text={
                Column {
                    OutlinedTextField(voiceFilter,{voiceFilter=it},singleLine=true,
                        label={Text("Filter name or language")})
                    Spacer(Modifier.height(8.dp))
                    LazyColumn(Modifier.heightIn(max=350.dp)) {
                        item { TextButton(onClick={voiceId="random";voicePicker=false}) {
                            Text("Auto voice for narration language")
                        }}
                        items(voices.filter {
                            it.name.contains(voiceFilter,true) ||
                                it.locale.displayLanguage.contains(voiceFilter,true)
                        }) { v ->
                            TextButton(onClick={voiceId=v.name;voicePicker=false},
                                modifier=Modifier.fillMaxWidth()) {
                                Column(Modifier.weight(1f)) {
                                    Text(v.name,fontSize=12.sp)
                                    Text(v.locale.displayLanguage,fontSize=11.sp,color=Muted)
                                }
                                if(v.name==voiceId) Icon(Icons.Outlined.Check,null,tint=Mint)
                            }
                        }
                    }
                }
            },
            confirmButton={ TextButton(onClick={voicePicker=false}) {Text("Done")} })
    }
}

@Composable private fun Section(s:String) {
    Text(s,fontSize=16.sp,fontWeight=FontWeight.Bold,color=Color.White)
}
@Composable private fun Banner() {
    Box(Modifier.fillMaxWidth().height(130.dp).background(
        Brush.linearGradient(listOf(Color(0xFF403475),Color(0xFF1C4360),Color(0xFF19172D))),
        RoundedCornerShape(20.dp)).padding(17.dp)) {
        Column {
            Icon(Icons.Outlined.AutoAwesome,null,tint=Mint)
            Spacer(Modifier.height(13.dp))
            Text("Ideas in. Shorts out.",fontWeight=FontWeight.ExtraBold,fontSize=23.sp)
            Text("Script → TTS → motion → MP4",fontSize=12.sp,color=Color(0xFFDAD6F8))
        }
        Icon(Icons.Outlined.SmartDisplay,null,Modifier.size(83.dp).align(Alignment.CenterEnd),
            tint=Color.White.copy(alpha=.16f))
    }
}
@Composable private fun AccountBox(title:String,caption:String,
    icon:androidx.compose.ui.graphics.vector.ImageVector,contents:@Composable ColumnScope.()->Unit) {
    Surface(color=Pane,shape=RoundedCornerShape(18.dp)) {
        Column(Modifier.fillMaxWidth().padding(16.dp)) {
            Row(verticalAlignment=Alignment.CenterVertically) {
                Icon(icon,null,tint=Purple)
                Spacer(Modifier.width(9.dp))
                Column { Text(title,fontWeight=FontWeight.Bold,fontSize=16.sp)
                    Text(caption,fontSize=11.sp,color=Muted) }
            }
            Spacer(Modifier.height(13.dp))
            contents()
        }
    }
}
private fun openSite(ctx:android.content.Context,url:String) {
    ctx.startActivity(Intent(Intent.ACTION_VIEW, Uri.parse(url)))
}
private fun fileUri(ctx:android.content.Context,file:File):Uri =
    FileProvider.getUriForFile(ctx,ctx.packageName+".files",file)
private fun viewVideo(ctx:android.content.Context,file:File) {
    ctx.startActivity(Intent(Intent.ACTION_VIEW).apply {
        setDataAndType(fileUri(ctx,file),"video/mp4")
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    })
}
private fun shareVideo(ctx:android.content.Context,file:File,title:String) {
    val share=Intent(Intent.ACTION_SEND).apply {
        type="video/mp4"
        putExtra(Intent.EXTRA_STREAM,fileUri(ctx,file))
        putExtra(Intent.EXTRA_TITLE,title)
        addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION)
    }
    ctx.startActivity(Intent.createChooser(share,"Share video to YouTube"))
}

