package com.shortsstudio.app

import android.content.Intent
import android.net.Uri
import android.os.Bundle
import androidx.activity.ComponentActivity
import androidx.activity.compose.setContent
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
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.FileProvider
import kotlinx.coroutines.launch
import kotlinx.coroutines.delay
import kotlinx.coroutines.CancellationException
import java.io.File

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
    val scope= rememberCoroutineScope()
    val snackbar= remember { SnackbarHostState() }
    var tab by rememberSaveable { mutableIntStateOf(0) }
    var videos by remember { mutableStateOf(MobileJobs.list(ctx)) }
    var cloudFilms by remember { mutableStateOf(emptyList<CloudConnection.CloudFilm>()) }
    var libraryError by remember { mutableStateOf("") }
    var libraryLoading by remember { mutableStateOf(false) }
    var libraryRefresh by remember { mutableIntStateOf(0) }
    var saving by remember { mutableStateOf("") }
    LaunchedEffect(tab, libraryRefresh) {
        if(tab!=1) return@LaunchedEffect
        do {
            videos=MobileJobs.list(ctx)
            libraryLoading=true
            try {
                if(CloudConnection.load(ctx)!=null) cloudFilms=CloudConnection.films(ctx)
                else cloudFilms=emptyList()
                libraryError=""
            } catch(e:CancellationException) { throw e }
            catch(e:Exception) { libraryError=e.message ?: "Could not load cloud films. Tap Refresh to retry." }
            finally { libraryLoading=false }
            delay(8000)
        } while(true)
    }

    fun notify(s:String) { scope.launch { snackbar.showSnackbar(s) } }

    fun saveCloud(film:CloudConnection.CloudFilm) {
        if(saving.isNotEmpty()) return
        saving=film.id
        scope.launch {
            try {
                CloudConnection.saveFilm(ctx,film)
                videos=MobileJobs.list(ctx)
                notify("Video saved on this phone. Open Library to preview or share.")
            } catch(e:CancellationException) { throw e }
            catch(e:Exception) { notify(e.message ?: "Could not save video. Try again from Library.") }
            finally {saving=""}
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
                    Text("Director · 0.6.0 · original films",fontSize=12.sp,color=Muted)
                }
                Spacer(Modifier.weight(1f))
                Icon(Icons.Outlined.PhoneAndroid,null,tint=Mint,modifier=Modifier.size(24.dp))
            }
            Spacer(Modifier.height(23.dp))

            when(tab) {
                0 -> if(CloudConnection.load(ctx)!=null) {
                    val config=CloudConnection.load(ctx)!!
                    CloudStudioPanel(config.first,config.second,Modifier.weight(1f)) { id ->
                        saveCloud(CloudConnection.CloudFilm(id,"ShortsStudio film","ready","",true))
                    }
                } else Column(Modifier.padding(vertical=22.dp)) {
                    Section("Your Director studio")
                    Spacer(Modifier.height(12.dp))
                    Text("Create original films with optional research, reference uploads and cloud daily creation.",color=Muted)
                    Spacer(Modifier.height(12.dp))
                    Text("Use your private setup page to connect this phone.",color=Muted)
                    Button(onClick={tab=2}) {Text("Connect in Accounts")}
                }
                1 -> Column(Modifier.fillMaxSize()) {
                    Row(verticalAlignment=Alignment.CenterVertically) {
                        Section("Your video library")
                        Spacer(Modifier.weight(1f))
                        IconButton(onClick={libraryRefresh++}) {Icon(Icons.Outlined.Refresh,"Refresh",tint=Purple)}
                    }
                    Text("Cloud films and videos saved on this phone",color=Muted,fontSize=12.sp)
                    if(libraryLoading) LinearProgressIndicator(Modifier.fillMaxWidth())
                    if(libraryError.isNotEmpty()) Text(libraryError,color=MaterialTheme.colorScheme.error,fontSize=12.sp)
                    if(saving.isNotEmpty()) Text("Saving video on this phone…",color=Purple)
                    LazyColumn(Modifier.weight(1f),verticalArrangement=Arrangement.spacedBy(11.dp),
                        contentPadding=PaddingValues(vertical=12.dp)) {
                        if(videos.isEmpty() && cloudFilms.isEmpty()) item {
                            Text(if(libraryLoading) "Checking your cloud films…" else if(libraryError.isNotEmpty())
                                "Cloud films could not be loaded. Tap Refresh to retry."
                                else "No videos yet. Create a film and return here.",color=Muted)
                        }
                        items(cloudFilms,key={"cloud-"+it.id}) { film ->
                            val local=videos.firstOrNull {it.second.name=="film-${film.id}.mp4"}?.second
                            Surface(color=Pane,shape=RoundedCornerShape(17.dp)) {
                                Column(Modifier.fillMaxWidth().padding(14.dp)) {
                                    Text(film.title,fontWeight=FontWeight.Bold)
                                    Text(if(local!=null) "MP4 · Saved on this phone" else film.message,color=Muted,fontSize=12.sp)
                                    if(local!=null) Row {
                                        TextButton(onClick={viewVideo(ctx,local)}) {Text("Preview")}
                                        TextButton(onClick={shareVideo(ctx,local,film.title)}) {Text("Share")}
                                    } else if(film.state=="ready" && film.available) {
                                        Button(onClick={saveCloud(film)},enabled=saving.isEmpty()) {Text("Save on this phone")}
                                    } else if(film.state=="ready") Text("Cloud file expired",color=Muted,fontSize=12.sp)
                                }
                            }
                        }
                        items(videos.filter { (_,file) -> cloudFilms.none {file.name=="film-${it.id}.mp4"} },
                            key={"local-"+it.second.absolutePath}) { (title,file) ->
                            Surface(color=Pane,shape=RoundedCornerShape(17.dp)) {
                                Column(Modifier.fillMaxWidth().padding(14.dp)) {
                                    Text(title,fontWeight=FontWeight.Bold)
                                    Text("MP4 · Saved on this phone",color=Muted,fontSize=12.sp)
                                    Row {
                                        TextButton(onClick={viewVideo(ctx,file)}) {Text("Preview")}
                                        TextButton(onClick={shareVideo(ctx,file,title)}) {Text("Share")}
                                    }
                                }
                            }
                        }
                    }
                }
                else -> Column(Modifier.verticalScroll(rememberScrollState())) {
                    Section("Connect your accounts")
                    Spacer(Modifier.height(8.dp))
                    Text("Your private cloud studio creates the animation and neural narration, then saves the finished MP4 on your phone.",
                        fontSize=13.sp,color=Muted)
                    Spacer(Modifier.height(17.dp))
                    AccountBox("Claude Code","Official CLI on your private Modal server",
                        Icons.Outlined.AutoAwesome) {
                        Button(onClick={
                            try { CloudConnection.openLogin(ctx) }
                            catch(e:Exception) { notify(e.message ?: "Pair your server first") }
                        },shape=RoundedCornerShape(12.dp)) { Text("Claude sign-in & pairing setup") }
                        Spacer(Modifier.height(8.dp))
                        OutlinedButton(onClick={openSite(ctx,"https://modal.com/playground/get_started")},
                            shape=RoundedCornerShape(12.dp)) { Text("Open Modal setup") }
                        OutlinedButton(onClick={openSite(ctx,"https://github.com/HadiMortada/ShortsStudio/tree/main/shorts-studio/cloud")},
                            shape=RoundedCornerShape(12.dp)) { Text("Get setup code") }
                        Text("Use your private setup link to connect the APK. Your existing Claude login stays active after an app update. Cloud rendering uses your Modal credits; Claude subscription limits apply separately.",
                            color=Muted,fontSize=12.sp)
                        TextButton(onClick={CloudConnection.clear(ctx);notify("Server disconnected from this phone. Claude's cloud login is unchanged.")}) { Text("Disconnect this phone") }
                    }
                    Spacer(Modifier.height(16.dp))
                    AccountBox("YouTube & narration","Manage in your Director studio",
                        Icons.Outlined.SmartDisplay) {
                        OutlinedButton(onClick={tab=0}) {Text("Open Director settings")}
                        Text("Choose your narrator and connect a YouTube channel in Director. Cloud Library keeps films made on the web; save an MP4 on this phone for offline preview and sharing.",color=Muted,fontSize=12.sp)
                    }
                    Spacer(Modifier.height(20.dp))
                }
            }
        }
    }

}

@Composable private fun Section(s:String) {
    Text(s,fontSize=16.sp,fontWeight=FontWeight.Bold,color=Color.White)
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



